"""صفحات المصدر: إرسال معلومة، ومتابعة الردود برمز سري.

لا تُحمَّل في هذه الصفحات شيفرة الترويسة الإضافية ولا الإعلانات ولا
القياس، ولا تُستخدم رسائل الجلسة، ولا يُحفظ عنوان المرسل في أي مكان.
"""

from __future__ import annotations

from django.http import Http404
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt, csrf_protect

from arcms.core.models import SiteSettings
from arcms.core.ratelimit import exceeded
from arcms.core.utils import client_ip

from . import services
from .uploads import InMemoryOnlyUploadHandler

PRIVATE_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)

# (عدد، ثوانٍ) لكل مصدر: بلاغات جديدة، ومحاولات رمز المتابعة.
LIMITS = {"submit": (6, 3600), "follow": (30, 3600)}


def _limited(scope: str, request) -> bool:
    limit, window = LIMITS[scope]
    return exceeded("tips-" + scope, client_ip(request), limit=limit, window=window)


def _render(request, template: str, ctx: dict, status: int = 200, *, index: bool = False):
    from arcms.public.views import ticker

    ctx = {"ticker": ticker(), "private_page": True, **ctx}
    response = render(request, template, ctx, status=status)
    response["Content-Security-Policy"] = PRIVATE_CSP
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"
    if not index:
        response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _enabled() -> SiteSettings:
    site = SiteSettings.load()
    if not site.tips_enabled:
        raise Http404
    return site


def _too_large(request) -> bool:
    try:
        return int(request.META.get("CONTENT_LENGTH") or 0) > services.MAX_REQUEST_BYTES
    except ValueError:
        return True


def _files(request) -> list[bytes]:
    return [f.read() for f in request.FILES.getlist("files")]


@csrf_exempt
def submit(request):
    _enabled()
    if request.method == "POST":
        # يجب ضبط معالج الرفع قبل أن يقرأ فحص CSRF الطلب؛ لذا يأتي الفحص بعده.
        request.upload_handlers = [InMemoryOnlyUploadHandler(request)]
    return _submit(request)


@csrf_protect
def _submit(request):
    error = ""
    form = {}
    status = 400
    if request.method == "POST":
        form = {k: request.POST.get(k, "").strip() for k in ("subject", "body", "contact")}
        if _too_large(request):
            error = "حجم المرفقات أكبر من 50 ميغابايت. أرسل أقل، أو قسّمها على عدة رسائل."
        elif request.POST.get("website"):  # فخ للبرامج الآلية: نتظاهر بالنجاح
            return _render(request, "tips/received.html", {"code": services.new_code()})
        elif _limited("submit", request):
            error = "وصلتنا رسائل كثيرة من الشبكة نفسها خلال وقت قصير. حاول بعد ساعة."
            status = 429
        elif len(form["body"]) < 10:
            error = "اكتب المعلومة التي تريد إيصالها (عشرة أحرف على الأقل)."
        elif len(request.FILES.getlist("files")) > services.MAX_FILES:
            error = f"يمكن إرفاق {services.MAX_FILES} ملفات على الأكثر في الرسالة الواحدة."
        else:
            try:
                tip, code = services.create_tip(
                    body=form["body"], subject=form["subject"], contact=form["contact"], files=_files(request)
                )
            except services.AttachmentRejected as exc:
                error = f"تعذّر قبول أحد المرفقات: {exc}"
            else:
                from .notify import notify_new_tip

                notify_new_tip(tip)
                return _render(request, "tips/received.html", {"code": code})
    return _render(
        request,
        "tips/submit.html",
        {"error": error, "form": form, "max_files": services.MAX_FILES},
        status=status if error else 200,
        index=not error,
    )


@csrf_exempt
def follow(request):
    _enabled()
    if request.method == "POST":
        request.upload_handlers = [InMemoryOnlyUploadHandler(request)]
    return _follow(request)


@csrf_protect
def _follow(request):
    tip = None
    error = ""
    sent = False
    code = ""
    status = 200
    if request.method == "POST":
        code = request.POST.get("code", "")
        if _limited("follow", request):
            error = "محاولات كثيرة خلال وقت قصير. حاول بعد ساعة."
            status = 429
        else:
            tip = services.find_tip(code)
            if tip is None:
                error = "الرمز غير صحيح. تأكد من نسخه كاملاً كما ظهر لك."
                status = 400
            elif "body" in request.POST:
                body = request.POST.get("body", "").strip()
                if _too_large(request):
                    error = "حجم المرفقات أكبر من 50 ميغابايت."
                elif not body:
                    error = "اكتب رسالتك."
                elif len(request.FILES.getlist("files")) > services.MAX_FILES:
                    error = f"يمكن إرفاق {services.MAX_FILES} ملفات على الأكثر."
                else:
                    try:
                        services.add_source_message(tip, body, _files(request))
                        sent = True
                    except services.AttachmentRejected as exc:
                        error = f"تعذّر قبول أحد المرفقات: {exc}"
    ctx = {"error": error, "sent": sent, "max_files": services.MAX_FILES}
    if tip is not None:
        # لا نعرض للمرسل نص بلاغه ولا رسائله السابقة: إن وقع الرمز في يد غيره
        # فلن يقرأ إلا ردود الغرفة. عدد رسائله يكفيه للتأكد من وصولها.
        ctx.update(
            {
                "tip": tip,
                "code": code,
                "replies": tip.messages.filter(from_source=False),
                "sent_count": 1 + tip.messages.filter(from_source=True).count(),
            }
        )
    return _render(request, "tips/follow.html", ctx, status=status)
