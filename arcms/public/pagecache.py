"""تخزين صفحات القرّاء: خبر عاجل يأتي بآلاف القرّاء في الثانية، والصفحة نفسها لكل من لا حساب له.

تُبنى الصفحة مرة ثم تُقدَّم جاهزة لكل قارئ حتى تتغير النسخة العامة (نشر مادة، عاجل، تعديل قسم أو
قائمة أو إعدادات…) أو تنتهي المدة. جزآن:

- المزخرف reader_cache على العروض التي لا تختلف صفحتها من قارئ لآخر: يقرر هل تُخزَّن الاستجابة،
  ويجعل الطلبات المتزامنة على صفحة غير مخزنة تنتظر طلباً واحداً يبنيها (في كل عملية).
- الوسيط ReaderCacheMiddleware في أول السلسلة: يحفظ الاستجابة النهائية بترويساتها (الأمان وCSP)
  ويقدّمها للطلبات التالية قبل أن تمر ببقية الوسطاء، فتكلّف الإصابة نصف ما تكلّفه لو مرت بها.
  طبقتان: ذاكرة العملية ثم الذاكرة المشتركة (Redis أو جدول القاعدة) لتشترك العمليات فيما بُني.

لا تُخزَّن صفحة إلا إن كانت واحدة للجميع: الطلب بلا جلسة (فلا رسائل ولا معاينة لمحرر)، والاستجابة
200 بلا كوكيز، ولم يُطلب فيها رمز CSRF (نموذج مرتبط بمتصفح بعينه). وتحمل Cache-Control يسمح
للوكيل (nginx) أو شبكة التوزيع بحفظها ثوانيَ معدودة أيضاً.
"""

from __future__ import annotations

import gzip
import threading
import time
from collections import OrderedDict
from functools import wraps
from urllib.parse import parse_qsl, urlencode

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.cache import patch_vary_headers

# معاملات لا تغيّر الصفحة (يقرؤها سكربت القياس في المتصفح): لا تفرّق المخزون.
IGNORED_PARAMS = ("utm_", "fbclid", "gclid", "mc_", "ref")
# مسارات لا تمر بالمخزن أصلاً، فلا يُسأل عنها المخزن المشترك في كل طلب
SKIP_PREFIXES = ("/static/", "/media/", "/studio", "/accounts", "/tips", "/a/v", "/card/", "/search", "/rss",
                 "/sitemap", "/news-sitemap", "/newsletter", "/poll/", "/push/subscribe", "/push/unsubscribe", "/s/")
DROP_HEADERS = {"content-length", "x-page-cache", "set-cookie", "date", "content-encoding"}
GZIP_MIN_BYTES = 1024
LOCAL_MAX_ENTRIES = 400
LOCAL_MAX_BYTES = 96 * 1024 * 1024
VERSION_MEMO_SECONDS = 1.0
STALE_GRACE = 60  # ثوانٍ بعد انتهاء المدة تبقى فيها النسخة السابقة متاحة ريثما تُبنى الجديدة
REBUILD_LOCK_SECONDS = 15
EDGE_SECONDS = 10  # ما يُسمح للوكيل أو شبكة التوزيع بحفظه؛ أقصر من مدة التطبيق لأن الوكيل لا يعرف النسخة


class _LocalStore:
    """LRU صغير في ذاكرة العملية، آمن مع الخيوط."""

    def __init__(self):
        self._data: OrderedDict[str, tuple[float, int, tuple]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            if item[0] < time.monotonic():
                self._drop(key)
                return None
            self._data.move_to_end(key)
            return item[2]

    def set(self, key: str, expires: float, value: tuple, size: int) -> None:
        with self._lock:
            if key in self._data:
                self._drop(key)
            self._data[key] = (expires, size, value)
            self._bytes += size
            while self._data and (len(self._data) > LOCAL_MAX_ENTRIES or self._bytes > LOCAL_MAX_BYTES):
                self._drop(next(iter(self._data)))

    def _drop(self, key: str) -> None:
        self._bytes -= self._data.pop(key)[1]

    def delete(self, key: str) -> None:
        with self._lock:
            if key in self._data:
                self._drop(key)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0


_pages = _LocalStore()  # الاستجابات النهائية بترويساتها (للوسيط)
_built = _LocalStore()  # محتوى العرض، لمن ينتظر من الطلبات المتزامنة في العملية نفسها
_building: dict[str, threading.Lock] = {}
_building_guard = threading.Lock()
_version = {"value": None, "until": 0.0}


def clear_local() -> None:
    _pages.clear()
    _built.clear()
    forget_version()


def forget_version() -> None:
    _version["until"] = 0.0


def _public_version() -> int:
    """نسخة المحتوى العام، يُسأل عنها المخزن المشترك مرة في الثانية على الأكثر لكل عملية."""
    now = time.monotonic()
    if _version["value"] is None or now >= _version["until"]:
        from arcms.content.signals import public_cache_version

        _version["value"] = public_cache_version()
        _version["until"] = now + VERSION_MEMO_SECONDS
    return _version["value"]


def _ttl() -> int:
    from arcms.core.models import SiteSettings

    return int(SiteSettings.load().home_cache_seconds or 0)


def _cache_key(request) -> str:
    params = [(k, v) for k, v in parse_qsl(request.META.get("QUERY_STRING", ""), keep_blank_values=True)
              if not k.startswith(IGNORED_PARAMS)]
    query = urlencode(sorted(params))
    return f"arcms:page:{_public_version()}:{request.scheme}://{request.get_host()}{request.path}?{query}"


def _anonymous(request) -> bool:
    return settings.SESSION_COOKIE_NAME not in request.COOKIES and "messages" not in request.COOKIES


def _personal(request) -> bool:
    """قارئ صوّت في استطلاع: تُعرض له النتائج. يُقدَّم له المخزون (السكربت يُظهر النتائج من الكوكي)،
    لكن ما يُبنى له لا يُخزَّن للآخرين."""
    return any(name.startswith("arcms_poll_") for name in request.COOKIES)


def _eligible(request) -> bool:
    return (request.method in ("GET", "HEAD") and _anonymous(request)
            and not request.path.startswith(SKIP_PREFIXES) and _ttl() > 0)


def _cacheable(request, response) -> bool:
    return (
        not _personal(request)
        and response.status_code == 200
        and not response.streaming
        and not response.cookies
        # الصفحة طلبت رمز CSRF (نموذج مرتبط بمتصفح هذا القارئ): لا تُعطى لغيره أبداً
        and not request.META.get("CSRF_COOKIE_NEEDS_UPDATE")
        and not response.has_header("Cache-Control")
    )


# --- الوسيط: الاستجابة النهائية ---


class ReaderCacheMiddleware:
    """بعد SecurityMiddleware مباشرة: يقدّم الصفحة المخزنة قبل بقية السلسلة، ويخزّن ما أجازه المزخرف
    بعد أن تكتمل ترويساته.

    بعد النشر (نسخة جديدة) لا تنهار الصفحات كلها على التطبيق معاً: يعيد بناءَ كل صفحة طلبٌ واحد في
    كل الخوادم (قفل في المخزن المشترك)، ويرى الباقون النسخة السابقة لحظات حتى تجهز الجديدة. وإن
    صارت الصفحة لا تُعرض (مادة سُحبت) تُحذف نسختها السابقة فوراً ولا تُقدَّم بعدها."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _eligible(request):
            return self.get_response(request)
        key = _page_key(request)
        version = _public_version()
        entry = _lookup_page(key, version)
        if entry is None:
            return self._build(request, key)
        if entry[0] == version and entry[1] > time.time():
            return _replay(entry, request, "hit")
        if not _claim_rebuild(key):
            return _replay(entry, request, "stale")
        try:
            return self._build(request, key)
        finally:
            _release_rebuild(key)

    def _build(self, request, key):
        response = self.get_response(request)
        ttl = getattr(response, "_reader_cache_ttl", None)
        if ttl and response.status_code == 200 and not response.cookies:
            _store_page(key, response, ttl, _public_version())
        elif response.status_code != 200:
            _forget_page(key)  # لم تعد الصفحة موجودة: لا تُقدَّم نسختها القديمة لأحد
        return response


def _page_key(request) -> str:
    params = [(k, v) for k, v in parse_qsl(request.META.get("QUERY_STRING", ""), keep_blank_values=True)
              if not k.startswith(IGNORED_PARAMS)]
    return f"arcms:resp:{request.scheme}://{request.get_host()}{request.path}?{urlencode(sorted(params))}"


def _replay(entry: tuple, request, state: str) -> HttpResponse:
    """الصفحة المخزنة، مضغوطة مسبقاً لمن يقبل gzip: يُضغط المحتوى مرة عند التخزين بدل كل طلب
    (Caddy وnginx لا يعيدان ضغط ما وصل مضغوطاً). لا أسرار في صفحات القرّاء فلا مجال لهجمات BREACH."""
    _version_, _until, content, headers, packed = entry
    use_gzip = packed and "gzip" in request.META.get("HTTP_ACCEPT_ENCODING", "")
    response = HttpResponse(packed if use_gzip else content)
    for name, value in headers:
        response[name] = value
    if use_gzip:
        response["Content-Encoding"] = "gzip"
    if packed:
        patch_vary_headers(response, ("Accept-Encoding",))
    response["X-Page-Cache"] = state
    return response


def _entry_size(entry: tuple) -> int:
    return len(entry[2]) + len(entry[4] or b"")


def _keep_locally(key: str, entry: tuple) -> None:
    # تبقى في ذاكرة العملية بعد انتهاء مدتها قليلاً: تُقدَّم «سابقةً» ريثما تُبنى الجديدة
    left = entry[1] - time.time() + STALE_GRACE
    if left > 0:
        _pages.set(key, time.monotonic() + left, entry, _entry_size(entry))


def _lookup_page(key: str, version: int):
    """أحدث نسخة معروفة: من العملية، أو من المخزن المشترك إن كانت فيه أحدث (بنتها عملية أخرى)."""
    entry = _pages.get(key)
    if entry is not None and entry[0] == version and entry[1] > time.time():
        return entry
    try:
        shared = cache.get(key)
    except Exception:  # noqa: BLE001 - تعطل المخزن المشترك لا يمنع القارئ من صفحته
        shared = None
    if shared and (entry is None or (shared[0], shared[1]) > (entry[0], entry[1])):
        entry = shared
        _keep_locally(key, entry)
    return entry


def _store_page(key: str, response, ttl: int, version: int) -> None:
    if response.has_header("Content-Encoding"):
        return  # ضغطها غيرنا: لا نعرف كيف نقدمها لمن لا يقبل الضغط
    headers = [(k, v) for k, v in response.items() if k.lower() not in DROP_HEADERS]
    content = response.content
    packed = gzip.compress(content, compresslevel=6, mtime=0) if len(content) >= GZIP_MIN_BYTES else None
    entry = (version, time.time() + ttl, content, headers, packed)
    _keep_locally(key, entry)
    try:
        cache.set(key, entry, ttl + STALE_GRACE)
    except Exception:  # noqa: BLE001
        pass


def _forget_page(key: str) -> None:
    _pages.delete(key)
    try:
        cache.delete(key)
    except Exception:  # noqa: BLE001
        pass


_rebuilding: set[str] = set()


def _claim_rebuild(key: str) -> bool:
    """من يعيد بناء الصفحة الآن؟ واحد في العملية، وواحد في كل الخوادم عبر المخزن المشترك."""
    with _building_guard:
        if key in _rebuilding:
            return False
        _rebuilding.add(key)
    try:
        if cache.add(f"{key}:rebuild", 1, REBUILD_LOCK_SECONDS):
            return True
    except Exception:  # noqa: BLE001 - بلا مخزن مشترك يكفي القفل المحلي
        return True
    with _building_guard:
        _rebuilding.discard(key)
    return False


def _release_rebuild(key: str) -> None:
    with _building_guard:
        _rebuilding.discard(key)
    try:
        cache.delete(f"{key}:rebuild")
    except Exception:  # noqa: BLE001
        pass


# --- المزخرف: ما يُخزَّن، وبناء واحد لكل صفحة ---


def reader_cache(view):
    """للعروض التي لا تختلف صفحتها من قارئ لآخر (بلا حساب)."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not _eligible(request):
            return view(request, *args, **kwargs)
        ttl = _ttl()
        key = _cache_key(request)
        with _building_guard:
            lock = _building.setdefault(key, threading.Lock())
        try:
            with lock:  # الطلبات المتزامنة على صفحة واحدة: يبنيها الأول وينتظر الباقون
                built = _built.get(key)
                if built is not None:
                    return _mark(HttpResponse(built[0], content_type=built[1]), ttl, "wait")
                response = view(request, *args, **kwargs)
                if not _cacheable(request, response):
                    return response
                content_type = response.get("Content-Type", "text/html; charset=utf-8")
                _built.set(key, time.monotonic() + ttl, (response.content, content_type), len(response.content))
                return _mark(response, ttl, "miss")
        finally:
            with _building_guard:
                if _building.get(key) is lock and not lock.locked():
                    _building.pop(key, None)

    return wrapper


def _mark(response, ttl: int, state: str):
    response["Cache-Control"] = f"public, max-age=0, s-maxage={min(ttl, EDGE_SECONDS)}"
    response["X-Page-Cache"] = state
    response._reader_cache_ttl = ttl
    return response
