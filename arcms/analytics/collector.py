"""تسجيل المشاهدة القادمة من صفحة القارئ."""

from __future__ import annotations

import hashlib
import re
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.db import IntegrityError
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


def record_view(*, ip: str | None, user_agent: str, path: str, referrer: str, article_id=None, category_id=None,
                utm_source: str = "", own_host: str = "") -> PageView | None:
    if is_bot(user_agent):
        return None
    own_host = own_host or (urlparse(settings.SITE_URL).hostname or "")
    source, ref_host = classify_source(referrer, own_host, utm_source)
    view = PageView.objects.create(
        path=path[:300],
        article_id=article_id,
        category_id=category_id,
        visitor=visitor_hash(ip or "", user_agent),
        source=source,
        referrer_host=ref_host[:120],
        device=device_class(user_agent),
    )
    if article_id:
        from django.db.models import F

        from arcms.content.models import Article

        Article.objects.filter(pk=article_id).update(view_count=F("view_count") + 1)
    return view
