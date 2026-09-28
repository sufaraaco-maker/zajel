"""فرض التحقق الثنائي: لا يصل مستخدم تلزمه المصادقة الثنائية إلى غرفة التحرير
قبل أن يفعّلها، ولا يُعدّ دخوله مكتملاً قبل إدخال الرمز."""

from django.contrib.auth import get_user_model, logout
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone

SESSION_VERIFIED = "arcms_2fa_ok"

_EXEMPT_PREFIXES = ("/static/", "/media/")


def session_fully_verified(request) -> bool:
    """جلسة أكملت التحقق الثنائي (إن لزمها) ولا تنتظر تغيير كلمة مرور مؤقتة."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    if user.has_2fa:
        return bool(request.session.get(SESSION_VERIFIED))
    return not user.requires_2fa and not user.must_change_password


class TwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path
        if user is not None and user.is_authenticated and path.startswith(("/studio", "/accounts")):
            response = self._enforce(request, user, path)
            if response is not None:
                return response
            if not request.session.get("arcms_seen") or request.session["arcms_seen"] < timezone.now().timestamp() - 300:
                request.session["arcms_seen"] = timezone.now().timestamp()
                get_user_model().objects.filter(pk=user.pk).update(last_seen_at=timezone.now())
        return self.get_response(request)

    def _enforce(self, request, user, path):
        allowed = {
            reverse("accounts:logout"),
            reverse("accounts:enroll"),
            reverse("accounts:password_change"),
        }
        if path.startswith(_EXEMPT_PREFIXES) or path in allowed:
            return None
        if user.has_2fa and not request.session.get(SESSION_VERIFIED):
            # جلسة دون تحقق رغم وجود جهاز: لا تُقبل.
            logout(request)
            return redirect("accounts:login")
        if user.must_change_password:
            return redirect("accounts:password_change")
        if not user.has_2fa and user.requires_2fa:
            return redirect("accounts:enroll")
        return None
