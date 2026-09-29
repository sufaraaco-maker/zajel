"""ترويسات الأمان وسياسة أمان المحتوى (CSP).

صفحات القرّاء لا تحمّل أي سكربت من طرف ثالث افتراضياً؛ الخطوط والأيقونات
والسكربتات كلها من الخادم نفسه، فلا تعرف أي شركة خارجية من يقرأ ماذا.
"""

import time

from django.conf import settings

from arcms.content.embeds import EMBED_FRAME_HOSTS

_FRAMES = " ".join(EMBED_FRAME_HOSTS)

STRICT_PUBLIC = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; font-src 'self'; connect-src 'self'; media-src 'self' https:; "
    f"frame-src {_FRAMES}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
RELAXED_PUBLIC = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; "
    "img-src 'self' data: https:; font-src 'self' https:; connect-src 'self' https:; media-src 'self' https:; "
    "frame-src https:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
STUDIO = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; font-src 'self'; connect-src 'self'; "
    f"frame-src 'self' {_FRAMES}; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"
)
PERMISSIONS = "geolocation=(), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()"


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        path = request.path
        if path.startswith(("/studio", "/accounts")):
            response.setdefault("Content-Security-Policy", STUDIO)
            # لا نسرّب روابط المسودات الداخلية إلى أي موقع يُفتح من غرفة التحرير.
            # (same-origin لا no-referrer: الأخيرة تجعل المتصفح يرسل Origin: null فيرفض فحص CSRF النماذج.)
            response["Referrer-Policy"] = "same-origin"
            response["Cache-Control"] = "no-store"
            response["X-Robots-Tag"] = "noindex, nofollow"
        elif not path.startswith(("/static/", "/media/")):
            user = getattr(request, "user", None)
            staff = bool(user is not None and user.is_authenticated)
            # جلسة من الطاقم لا تشغّل شيفرة طرف ثالث أبداً: سكربت إعلان يعمل بصلاحيات المحرر
            # يستطيع قراءة المسودات أو التصرف في غرفة التحرير باسمه.
            relaxed = not staff and _third_party_enabled()
            response.setdefault("Content-Security-Policy", RELAXED_PUBLIC if relaxed else STRICT_PUBLIC)
        response.setdefault("Permissions-Policy", PERMISSIONS)
        response.setdefault("X-Content-Type-Options", "nosniff")
        if not settings.DEBUG:
            response.setdefault("Cross-Origin-Resource-Policy", "same-site")
        return response


_ads_memo = {"value": False, "until": 0.0}


def forget_ads(*args, **kwargs) -> None:
    """إعلان بشيفرة خارجية أُضيف أو أزيل: تتغير سياسة المحتوى في الصفحات العامة."""
    from django.core.cache import cache

    cache.delete("arcms:ads-html")
    _ads_memo["until"] = 0.0


def _third_party_enabled() -> bool:
    from arcms.core.models import AdSlot, SiteSettings

    site = SiteSettings.load()
    if site.custom_head_html.strip():
        return True
    # يُسأل في كل صفحة: جواب المخزن المشترك يُحفظ في العملية ثواني قليلة (مثل إعدادات الموقع)
    now = time.monotonic()
    if now < _ads_memo["until"]:
        return _ads_memo["value"]
    from django.core.cache import cache

    key = "arcms:ads-html"
    has_ads = cache.get(key)
    if has_ads is None:
        has_ads = AdSlot.objects.filter(is_active=True).exclude(html="").exists()
        cache.set(key, has_ads, 60)
    _ads_memo.update(value=has_ads, until=now + SiteSettings.LOCAL_TTL)
    return has_ads
