"""محاكاة قرّاء كُثر يدخلون موقع arcms معاً: لقياس ما يتحمله الخادم قبل أن يختبره خبر عاجل.

كل قارئ يتصرف كمتصفح جديد: يطلب الصفحة، ثم ملفاتها (الأنماط والسكربت والخط والصور الأولى)، ثم
طلب القياس الصغير (/a/v) كما يفعل public.js. لكل قارئ عنوان مختلف في ترويسة X-Simulated-IP
(يمررها وكيل الاختبار إلى التطبيق) كي لا تجمعهم حدود المعدل كأنهم زائر واحد.

طريقتان:
  spike   ‏N قارئاً يصلون خلال نافذة قصيرة (0 = في اللحظة نفسها) إلى صفحة واحدة: أثر إشعار عاجل.
  browse  ‏N قارئاً متزامنين يتنقلون مدة محددة: الرئيسية ثم مادة ثم أخرى، مع وقت قراءة بين الصفحات.

أمثلة:
  python loadtest/readers.py spike  --base http://127.0.0.1:8080 --readers 10000 --window 5 --path /post/1/
  python loadtest/readers.py browse --base http://127.0.0.1:8080 --readers 10000 --duration 120

لا تشغّله على موقع لا تملكه: هو حمل حقيقي. المتطلبات: pip install -r loadtest/requirements.txt
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from urllib.parse import urljoin, urlsplit

import aiohttp

ASSET_RE = re.compile(r'<(?:link|script)\b[^>]*?\b(?:href|src)="([^"]+\.(?:css|js|woff2))"')
IMG_RE = re.compile(r'<img\b[^>]*?\bsrc="([^"]+)"')
POST_RE = re.compile(r'href="(/post/\d+/[^"#?]*)"')
ARTICLE_RE = re.compile(r'data-article="(\d+)"')
CATEGORY_RE = re.compile(r'data-category="(\d+)"')
UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Mobile Safari/537.36")
KINDS = ("page", "asset", "api", "beacon")


def random_ip(rnd: random.Random) -> str:
    return f"{rnd.randint(11, 223)}.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}"


class Stats:
    def __init__(self):
        self.latency = defaultdict(list)       # نوع الطلب ← أزمنة الاستجابة الناجحة
        self.outcome = defaultdict(Counter)    # نوع الطلب ← الحالة (200، 502، timeout…)
        self.finished = []                     # لحظة انتهاء كل طلب (للتدفق في الثانية)
        self.readers = []                      # زمن اكتمال الصفحة لكل قارئ نجح
        self.readers_failed = 0
        self.samples = {}                      # نص أول خطأ من كل نوع (لتمييز عطل الخادم من حدود جهاز القياس)

    def add(self, kind: str, seconds: float, outcome):
        self.outcome[kind][str(outcome)] += 1
        self.finished.append(time.time())
        if isinstance(outcome, int) and outcome < 400:
            self.latency[kind].append(seconds)

    def dump(self) -> dict:
        return {"latency": dict(self.latency), "outcome": {k: dict(v) for k, v in self.outcome.items()},
                "finished": self.finished, "readers": self.readers, "readers_failed": self.readers_failed,
                "samples": self.samples}


async def fetch(session, stats, kind, url, *, ip, method="GET", data=None, timeout=30.0, referer=None):
    headers = {"X-Simulated-IP": ip, "User-Agent": UA, "Accept-Language": "ar"}
    if referer:
        headers["Referer"] = referer
    if data is not None:
        headers["Content-Type"] = "application/json"
    t0 = time.perf_counter()
    try:
        async with session.request(method, url, data=data, headers=headers, allow_redirects=method == "GET",
                                   timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            body = await resp.read()
            stats.add(kind, time.perf_counter() - t0, resp.status)
            if kind == "page" and resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)  # نقرأ روابط الملفات من الصفحة؛ الباقي يبقى مضغوطاً كما وصل
            return resp.status, body
    except asyncio.TimeoutError:
        stats.add(kind, time.perf_counter() - t0, "timeout")
    except aiohttp.ClientError as exc:
        stats.add(kind, time.perf_counter() - t0, type(exc).__name__)
        stats.samples.setdefault(type(exc).__name__, str(exc)[:200])
    return None, b""


def page_parts(html: str, base: str, images: int) -> tuple[list[str], dict]:
    assets = list(dict.fromkeys(ASSET_RE.findall(html)))
    assets += [src for src in IMG_RE.findall(html) if src.startswith(("/", base))][:images]
    beacon = {"p": "", "r": "", "a": "", "c": "", "u": ""}
    if m := ARTICLE_RE.search(html):
        beacon["a"] = m.group(1)
    if m := CATEGORY_RE.search(html):
        beacon["c"] = m.group(1)
    return [urljoin(base, a) for a in assets], beacon


async def read_page(session, stats, args, url, ip, cache: dict, referer=None) -> tuple[bool, str]:
    """صفحة كاملة كما يطلبها متصفح جديد. تعيد (نجحت؟، نص الصفحة)."""
    status, body = await fetch(session, stats, "page", url, ip=ip, timeout=args.timeout, referer=referer)
    if status != 200:
        return False, ""
    html = body.decode("utf-8", "replace")
    assets, beacon = page_parts(html, args.base, args.images)
    if not args.no_assets:
        todo = [a for a in assets if a not in cache] if args.browser_cache else assets
        step = max(1, args.parallel)  # HTTP/2 اتصال واحد للموقع؛ HTTP/1.1 حتى ستة اتصالات
        for i in range(0, len(todo), step):
            await asyncio.gather(*(fetch(session, stats, "asset", a, ip=ip, timeout=args.timeout)
                                   for a in todo[i:i + step]))
        cache.update(dict.fromkeys(todo))
    if "data-push" in html:  # المتصفحات التي تدعم الإشعارات تسأل عن إعدادها في كل صفحة
        await fetch(session, stats, "api", urljoin(args.base, "/push/config"), ip=ip, timeout=args.timeout)
    if not args.no_beacon:
        beacon["p"] = urlsplit(url).path
        await fetch(session, stats, "beacon", urljoin(args.base, "/a/v"), ip=ip, method="POST",
                    data=json.dumps(beacon), timeout=args.timeout)
    return True, html


async def spike_reader(session, stats, args, delay, rnd):
    await asyncio.sleep(delay)
    t0 = time.perf_counter()
    ok, _ = await read_page(session, stats, args, urljoin(args.base, args.path), random_ip(rnd), {})
    if ok:
        stats.readers.append(time.perf_counter() - t0)
    else:
        stats.readers_failed += 1


async def browse_reader(session, stats, args, delay, rnd, stop_at):
    await asyncio.sleep(delay)
    ip, cache, links, referer = random_ip(rnd), {}, [], None
    url = urljoin(args.base, "/")
    while time.time() < stop_at:
        t0 = time.perf_counter()
        ok, html = await read_page(session, stats, args, url, ip, cache, referer=referer)
        if ok:
            stats.readers.append(time.perf_counter() - t0)
            links = POST_RE.findall(html) or links
        else:
            stats.readers_failed += 1
        referer = url
        # الرئيسية أحياناً، ومادة من الروابط التي ظهرت غالباً
        url = urljoin(args.base, rnd.choice(links)) if links and rnd.random() > 0.15 else urljoin(args.base, "/")
        await asyncio.sleep(rnd.uniform(args.think_min, args.think_max))


def _sessions(args, part: int) -> list[aiohttp.ClientSession]:
    """جلسة لكل عنوان مصدر. على جهاز واحد تنفد منافذ 127.0.0.1 قبل أن يتعب الخادم (نحو 28 ألف
    اتصال)، فيوزَّع القرّاء على عناوين 127.0.1.x حين يكون الموقع على الجهاز نفسه."""
    host = urlsplit(args.base).hostname or ""
    addrs = [None]
    if args.local_addrs and host.startswith("127."):
        addrs = [(f"127.0.{1 + part}.{n}", 0) for n in range(1, args.local_addrs + 1)]
    return [aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=0, ttl_dns_cache=600, keepalive_timeout=15,
                                                                 local_addr=addr),
                                  auto_decompress=False, headers={"Accept-Encoding": "gzip"})
            for addr in addrs]


async def run_part(args, count: int, seed: int, part: int = 0) -> dict:
    stats = Stats()
    rnd = random.Random(seed)
    sessions = _sessions(args, part)
    try:
        pick = lambda i: sessions[i % len(sessions)]  # noqa: E731
        if args.mode == "spike":
            tasks = [spike_reader(pick(i), stats, args, rnd.uniform(0, args.window), rnd) for i in range(count)]
        else:
            stop_at = time.time() + args.duration
            tasks = [browse_reader(pick(i), stats, args, rnd.uniform(0, args.ramp), rnd, stop_at)
                     for i in range(count)]
        await asyncio.gather(*tasks)
    finally:
        for s in sessions:
            await s.close()
    return stats.dump()


def _part(args, count, seed, part):
    return asyncio.run(run_part(args, count, seed, part))


def pct(values, p):
    if not values:
        return float("nan")
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


def report(parts: list[dict], args, wall: float) -> dict:
    latency, outcome, finished, readers, failed, samples = defaultdict(list), defaultdict(Counter), [], [], 0, {}
    for part in parts:
        samples.update(part["samples"])
        for k, v in part["latency"].items():
            latency[k] += v
        for k, v in part["outcome"].items():
            outcome[k].update(v)
        finished += part["finished"]
        readers += part["readers"]
        failed += part["readers_failed"]
    per_second = Counter(int(t) for t in finished)
    summary = {"mode": args.mode, "readers": args.readers, "wall_seconds": round(wall, 1),
               "requests": sum(sum(c.values()) for c in outcome.values()),
               "peak_requests_per_second": max(per_second.values()) if per_second else 0,
               "mean_requests_per_second": round(len(finished) / wall, 1) if wall else 0,
               "pages_ok": len(readers), "pages_failed": failed, "kinds": {}, "error_samples": samples}
    ms = lambda s: round(s * 1000)  # noqa: E731
    for kind in KINDS:
        if kind not in outcome:
            continue
        lat = latency[kind]
        total = sum(outcome[kind].values())
        good = sum(n for code, n in outcome[kind].items() if code.isdigit() and int(code) < 400)
        summary["kinds"][kind] = {
            "count": total, "ok": good, "failed": total - good, "outcomes": dict(outcome[kind]),
            "p50_ms": ms(pct(lat, 50)), "p90_ms": ms(pct(lat, 90)), "p99_ms": ms(pct(lat, 99)),
            "max_ms": ms(max(lat)) if lat else None,
        }
    summary["page_complete_ms"] = {"p50": ms(pct(readers, 50)), "p95": ms(pct(readers, 95)),
                                   "p99": ms(pct(readers, 99))} if readers else {}
    return summary


def print_summary(s: dict) -> None:
    ok_pct = 100 * s["pages_ok"] / max(1, s["pages_ok"] + s["pages_failed"])
    print(f"\n{s['mode']}: {s['readers']} قارئ · {s['wall_seconds']} ث · {s['requests']} طلب "
          f"(ذروة {s['peak_requests_per_second']}/ث، متوسط {s['mean_requests_per_second']}/ث)")
    print(f"صفحات اكتملت: {s['pages_ok']} · فشلت: {s['pages_failed']} ({ok_pct:.2f}% نجاح)")
    if s["page_complete_ms"]:
        pc = s["page_complete_ms"]
        print(f"زمن اكتمال الصفحة بملفاتها: p50 {pc['p50']} ms · p95 {pc['p95']} ms · p99 {pc['p99']} ms")
    print(f"{'النوع':8} {'العدد':>8} {'فشل':>7} {'p50':>7} {'p90':>7} {'p99':>7} {'أقصى':>7}   النتائج")
    for kind, k in s["kinds"].items():
        print(f"{kind:8} {k['count']:>8} {k['failed']:>7} {k['p50_ms']:>7} {k['p90_ms']:>7} {k['p99_ms']:>7} "
              f"{k['max_ms'] if k['max_ms'] is not None else '-':>7}   {k['outcomes']}")
    for name, text in s["error_samples"].items():
        print(f"  {name}: {text}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("mode", choices=("spike", "browse"))
    parser.add_argument("--base", required=True, help="عنوان الموقع، مثل https://news.example.org")
    parser.add_argument("--readers", type=int, default=1000)
    parser.add_argument("--path", default="/", help="الصفحة التي يصلها الجميع (spike)")
    parser.add_argument("--window", type=float, default=5, help="ثوانٍ يتوزع عليها وصول القرّاء (spike)")
    parser.add_argument("--duration", type=float, default=60, help="مدة التنقل بالثواني (browse)")
    parser.add_argument("--ramp", type=float, default=20, help="ثوانٍ يتوزع عليها دخول القرّاء (browse)")
    parser.add_argument("--think-min", type=float, default=5)
    parser.add_argument("--think-max", type=float, default=20)
    parser.add_argument("--images", type=int, default=2, help="عدد الصور الأولى التي يطلبها كل قارئ")
    parser.add_argument("--no-assets", action="store_true", help="الصفحات فقط، بلا الأنماط والسكربت والصور")
    parser.add_argument("--no-beacon", action="store_true")
    parser.add_argument("--no-browser-cache", dest="browser_cache", action="store_false",
                        help="يعيد القارئ طلب الملفات في كل صفحة (browse)")
    parser.add_argument("--parallel", type=int, default=2, help="طلبات الملفات المتوازية لكل قارئ")
    parser.add_argument("--local-addrs", type=int, default=0,
                        help="توزيع الاتصالات على عدد من عناوين 127.0.x.y (حين يكون الموقع على الجهاز نفسه)")
    parser.add_argument("--timeout", type=float, default=30, help="ثوانٍ قبل أن يعدّ الطلب فاشلاً")
    parser.add_argument("--procs", type=int, default=1, help="عمليات توليد الحمل (نواة لكل عملية)")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", help="حفظ الملخص JSON في هذا الملف")
    args = parser.parse_args(argv)
    args.base = args.base.rstrip("/")

    shares = [args.readers // args.procs + (1 if i < args.readers % args.procs else 0) for i in range(args.procs)]
    t0 = time.time()
    if args.procs == 1:
        parts = [asyncio.run(run_part(args, shares[0], args.seed))]
    else:
        with ProcessPoolExecutor(args.procs) as pool:
            parts = list(pool.map(_part, [args] * args.procs, shares, [args.seed + i for i in range(args.procs)],
                                  range(args.procs)))
    summary = report(parts, args, time.time() - t0)
    print_summary(summary)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
    return 0 if summary["pages_failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
