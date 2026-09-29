"""تسجيل المشاهدة القادمة من صفحة القارئ."""

from __future__ import annotations

import atexit
import hashlib
import logging
import os
import re
import secrets
import threading
import time
from collections import Counter
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError, close_old_connections
from django.db.models import F
from django.utils import timezone

from .models import DailySalt, PageView

_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|facebookexternalhit|embedly|preview|headless|python-requests|curl|wget|monitor|lighthouse",
    re.I,
)
_TABLET_RE = re.compile(r"ipad|tablet|kindle|silk|playbook|(android(?!.*mobile))", re.I)
_MOBILE_RE = re.compile(r"mobi|iphone|ipod|android|blackberry|opera mini|iemobile", re.I)

SEARCH_HOSTS = ("google.", "bing.", "yahoo.", "duckduckgo.", "yandex.", "baidu.", "ecosia.", "qwant.")
SOCIAL_HOSTS = ("facebook.", "fb.", "t.co", "twitter.", "x.com", "instagram.", "youtube.", "tiktok.", "linkedin.", "reddit.", "threads.")
MESSAGING_HOSTS = ("t.me", "telegram.", "web.whatsapp.", "wa.me", "whatsapp.", "signal.")


# الملح في ذاكرة العملية فقط، لا في الذاكرة المؤقتة المشتركة: هذه قد تكون جدولاً في
# القاعدة يبقى فيه الملح بعد حذفه من DailySalt، فيدخل النسخ الاحتياطية.
_salts: dict = {}


def daily_salt(day=None) -> str:
    day = day or timezone.localdate()
    salt = _salts.get(day)
    if salt:
        return salt
    obj = DailySalt.objects.filter(day=day).first()
    if obj is None:
        try:
            obj = DailySalt.objects.create(day=day, salt=secrets.token_hex(32))
        except IntegrityError:
            obj = DailySalt.objects.get(day=day)
    _salts.clear()  # لا يبقى في الذاكرة إلا ملح اليوم
    _salts[day] = obj.salt
    return obj.salt


def visitor_hash(ip: str, user_agent: str) -> str:
    raw = f"{daily_salt()}|{ip}|{user_agent}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def device_class(ua: str) -> str:
    if _TABLET_RE.search(ua):
        return PageView.Device.TABLET
    if _MOBILE_RE.search(ua):
        return PageView.Device.MOBILE
    return PageView.Device.DESKTOP


def classify_source(referrer: str, own_host: str, utm_source: str = "") -> tuple[str, str]:
    utm = (utm_source or "").lower()
    if utm in ("push", "webpush"):
        return PageView.Source.PUSH, ""
    if utm in ("newsletter", "email"):
        return PageView.Source.NEWSLETTER, ""
    if utm in ("telegram", "whatsapp"):
        return PageView.Source.MESSAGING, utm
    if not referrer:
        return PageView.Source.DIRECT, ""
    host = (urlparse(referrer).hostname or "").lower().removeprefix("www.").removeprefix("m.")
    if not host:
        return PageView.Source.DIRECT, ""
    if host == own_host.removeprefix("www."):
        return PageView.Source.INTERNAL, ""
    if any(host.startswith(h) or f".{h}" in f".{host}" for h in MESSAGING_HOSTS):
        return PageView.Source.MESSAGING, host
    if any(h in host for h in SEARCH_HOSTS):
        return PageView.Source.SEARCH, host
    if any(host.startswith(h) or h in host for h in SOCIAL_HOSTS):
        return PageView.Source.SOCIAL, host
    return PageView.Source.REFERRAL, host


def is_bot(ua: str) -> bool:
    return not ua or bool(_BOT_RE.search(ua))


def _view_fields(*, ip, user_agent, path, referrer, article_id, category_id, utm_source, own_host) -> dict | None:
    if is_bot(user_agent):
        return None
    own_host = own_host or (urlparse(settings.SITE_URL).hostname or "")
    source, ref_host = classify_source(referrer, own_host, utm_source)
    return {
        "path": path[:300], "article_id": article_id, "category_id": category_id,
        "visitor": visitor_hash(ip or "", user_agent), "source": source,
        "referrer_host": ref_host[:120], "device": device_class(user_agent),
    }


def _bump_views(counts: Counter) -> None:
    from arcms.content.models import Article

    for article_id, n in counts.items():
        Article.objects.filter(pk=article_id).update(view_count=F("view_count") + n)


def record_view(*, ip: str | None, user_agent: str, path: str, referrer: str, article_id=None, category_id=None,
                utm_source: str = "", own_host: str = "") -> PageView | None:
    """يسجّل المشاهدة فوراً (للأدوات والاختبارات). طلبات القرّاء تمر بـ queue_view."""
    fields = _view_fields(ip=ip, user_agent=user_agent, path=path, referrer=referrer, article_id=article_id,
                          category_id=category_id, utm_source=utm_source, own_host=own_host)
    if fields is None:
        return None
    view = PageView.objects.create(**fields)
    if article_id:
        _bump_views(Counter({article_id: 1}))
    return view


# --- طابور المشاهدات ---
# خبر عاجل يعني آلاف المشاهدات في الثانية للمادة نفسها. كتابة كل منها وحدها تعني آلاف الإدراجات،
# وآلاف التحديثات على صف المادة نفسه وكلها تنتظر قفله. الطابور يجمعها في ذاكرة العملية ويكتبها كل
# ثانية دفعة واحدة، مع تحديث واحد لعدّاد كل مادة. أقصى ما يضيع إن انهارت العملية فجأة: ثانية من الأرقام.

_queue: list[dict] = []
_queue_lock = threading.Lock()
_flusher = {"pid": None}
FLUSH_SECONDS = 1.0
MAX_QUEUED = 50_000  # فوق هذا (قاعدة متعطلة) تُهمل المشاهدات بدل أن تستنزف الذاكرة
log = logging.getLogger(__name__)


def queue_view(**kwargs) -> None:
    fields = _view_fields(**{"category_id": None, "article_id": None, "utm_source": "", "own_host": "", **kwargs})
    if fields is None:
        return
    if not getattr(settings, "ARCMS_ANALYTICS_QUEUE", True):
        PageView.objects.create(**fields)
        if fields["article_id"]:
            _bump_views(Counter({fields["article_id"]: 1}))
        return
    with _queue_lock:
        if len(_queue) >= MAX_QUEUED:
            return
        _queue.append(fields)
    _ensure_flusher()


def flush_views() -> int:
    with _queue_lock:
        batch = _queue[:]
        _queue.clear()
    if not batch:
        return 0
    PageView.objects.bulk_create([PageView(**f) for f in batch], batch_size=1000)
    _bump_views(Counter(f["article_id"] for f in batch if f["article_id"]))
    return len(batch)


def _flush_loop() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        try:
            close_old_connections()
            flush_views()
        except Exception:  # noqa: BLE001 - القاعدة قد تتعطل لحظة؛ الدفعة التالية تحاول من جديد
            log.exception("تعذّر حفظ دفعة المشاهدات")


def _ensure_flusher() -> None:
    pid = os.getpid()
    if _flusher["pid"] == pid:
        return
    with _queue_lock:
        if _flusher["pid"] == pid:
            return
        _flusher["pid"] = pid  # بعد fork يبدأ كل عامل خيطه الخاص
        threading.Thread(target=_flush_loop, name="arcms-views", daemon=True).start()


@atexit.register
def _flush_at_exit() -> None:
    try:
        flush_views()
    except Exception:  # noqa: BLE001
        pass
