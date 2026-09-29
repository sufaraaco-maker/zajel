"""الدخول على مرحلتين: كلمة المرور ثم رمز التطبيق، وتفعيل الجهاز لأول مرة."""

from __future__ import annotations

import time

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from arcms.audit.models import Action
from arcms.audit.services import Actor, record
from arcms.core.utils import client_ip, is_safe_redirect

from . import totp
from .forms import EnrollForm, LoginForm, OTPForm, PasswordChange
from .middleware import SESSION_VERIFIED
from .models import LoginAttempt, User

PENDING_KEY = "arcms_pending_user"
PENDING_TTL = 300


def _actor(request, user=None, label=""):
    return Actor(
        user=user,
        ip=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:200],
        label=label or (str(user) if user else "زائر"),
    )


def _finish_login(request, user: User):
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.cycle_key()
    request.session[SESSION_VERIFIED] = user.has_2fa
    LoginAttempt.objects.create(username=user.username, ip=client_ip(request), success=True)
    record(Action.LOGIN, user, message="دخول ناجح" + (" بالتحقق الثنائي" if user.has_2fa else ""), actor=_actor(request, user))
    nxt = request.session.pop("arcms_next", "") or request.GET.get("next", "")
    return redirect(nxt if is_safe_redirect(nxt) else settings.LOGIN_REDIRECT_URL)


@never_cache
@csrf_protect
@sensitive_post_parameters("password")
def login_view(request):
    if request.user.is_authenticated:
        return redirect(settings.LOGIN_REDIRECT_URL)
    form = LoginForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        ident = form.cleaned_data["username"].strip()
        ip = client_ip(request)
        user_obj = User.objects.filter(Q(username__iexact=ident) | Q(email__iexact=ident)).first()
        username = user_obj.username if user_obj else ident
        if LoginAttempt.is_locked(username, ip):
            error = f"تعددت المحاولات الفاشلة. أُقفل الدخول مؤقتاً لمدة {settings.ARCMS_LOGIN_LOCK_MINUTES} دقيقة."
            record(Action.SECURITY, message=f"محاولة دخول أثناء القفل: {username}", actor=_actor(request))
        else:
            user = authenticate(request, username=username, password=form.cleaned_data["password"])
            if user is None or not user.is_active:
                LoginAttempt.objects.create(username=username, ip=ip, success=False)
                record(
                    Action.LOGIN_FAILED,
                    message=f"كلمة مرور خاطئة لـ «{username}»",
                    object_type="accounts.user",
                    object_id=str(user_obj.pk) if user_obj else "",
                    actor=_actor(request),
                )
                error = "اسم المستخدم أو كلمة المرور غير صحيحة."
            elif user.has_2fa:
                request.session[PENDING_KEY] = {"id": user.pk, "t": time.time()}
                if is_safe_redirect(request.GET.get("next", "")):
                    request.session["arcms_next"] = request.GET["next"]
                return redirect("accounts:verify")
            else:
                return _finish_login(request, user)
    return render(request, "accounts/login.html", {"form": form, "error": error})


@never_cache
@csrf_protect
def verify_view(request):
    pending = request.session.get(PENDING_KEY)
    if not pending or time.time() - pending.get("t", 0) > PENDING_TTL:
        request.session.pop(PENDING_KEY, None)
        messages.error(request, "انتهت مهلة التحقق. أعد الدخول.")
        return redirect("accounts:login")
    user = User.objects.filter(pk=pending["id"], is_active=True).first()
    if user is None:
        return redirect("accounts:login")
    form = OTPForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        ip = client_ip(request)
        if LoginAttempt.is_locked(user.username, ip):
            error = "تعددت المحاولات الفاشلة. أُقفل الدخول مؤقتاً."
        else:
            code = form.cleaned_data["code"].strip()
            # من اختار «مفتاح الأمان فقط» لا يُقبل منه رمز التطبيق (قابل للاصطياد)، بل رموز الاسترداد للطوارئ
            ok = False if user.uses_keys_only else user.verify_totp(code)
            used_recovery = False
            if not ok and len("".join(c for c in code if c.isalnum())) == 10:
                ok = used_recovery = user.use_recovery_code(code)
            if ok:
                request.session.pop(PENDING_KEY, None)
                if used_recovery:
                    record(Action.TWO_FA, user, message="دخول برمز استرداد", actor=_actor(request, user))
                    messages.warning(request, f"استخدمت رمز استرداد. تبقّى لديك {len(user.recovery_codes)} رمزاً.")
                return _finish_login(request, user)
            LoginAttempt.objects.create(username=user.username, ip=ip, success=False, stage="otp")
            record(Action.LOGIN_FAILED, user, message="رمز تحقق ثنائي خاطئ", actor=_actor(request, user))
            error = "الرمز غير صحيح أو انتهت صلاحيته."
    has_keys = user.security_keys.exists()
    return render(request, "accounts/verify.html", {"form": form, "error": error, "pending_user": user,
                                                    "has_keys": has_keys, "keys_only": has_keys and user.keys_only})


def _check_current_factor(request, user: User, code: str) -> str:
    """نقل التحقق إلى هاتف جديد يتطلب إثبات حيازة الجهاز الحالي (أو رمز استرداد)."""
    ip = client_ip(request)
    if LoginAttempt.is_locked(user.username, ip):
        return "تعددت المحاولات الفاشلة. أُقفل التحقق مؤقتاً."
    code = (code or "").strip()
    if code and (user.verify_totp(code) or (len("".join(c for c in code if c.isalnum())) == 10 and user.use_recovery_code(code))):
        return ""
    LoginAttempt.objects.create(username=user.username, ip=ip, success=False, stage="otp")
    record(Action.LOGIN_FAILED, user, message="رمز خاطئ عند محاولة نقل التحقق الثنائي", actor=_actor(request, user))
    return "رمز الجهاز الحالي غير صحيح."


@never_cache
@login_required
@sensitive_post_parameters("current", "code")
def enroll_view(request):
    user: User = request.user
    replacing = user.has_2fa
    if replacing and request.method == "GET" and not request.GET.get("reset"):
        return render(request, "accounts/enroll_done.html", {"codes": None})
    secret = request.session.get("arcms_enroll_secret")
    if not secret or request.method == "GET":
        secret = totp.generate_secret()
        request.session["arcms_enroll_secret"] = secret
    uri = totp.provisioning_uri(secret, user.username, settings.ARCMS_2FA_ISSUER)
    qr_svg = segno.make(uri, error="m").svg_inline(scale=5, dark="#111418", light="#ffffff")
    form = EnrollForm(request.POST or None)
    error = ""
    if request.method == "POST" and form.is_valid():
        counter = totp.verify(secret, form.cleaned_data["code"])
        if replacing:
            error = _check_current_factor(request, user, form.cleaned_data["current"])
        if not error and counter is None:
            error = "الرمز غير صحيح. تأكد من ضبط وقت الهاتف تلقائياً وأعد المحاولة."
        if not error:
            codes = totp.generate_recovery_codes()
            user.totp_secret = secret
            user.totp_confirmed_at = timezone.now()
            user.totp_last_counter = counter
            user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
            user.save()
            request.session.pop("arcms_enroll_secret", None)
            request.session[SESSION_VERIFIED] = True
            if replacing:
                # الجلسات المفتوحة على الجهاز القديم لا تبقى صالحة بعد نقله.
                user.end_all_sessions(request)
            record(
                Action.TWO_FA, user,
                message="نقل التحقق الثنائي إلى جهاز جديد" if replacing else "تفعيل التحقق الثنائي",
                actor=_actor(request, user),
            )
            return render(request, "accounts/enroll_done.html", {"codes": codes})
    grouped = " ".join(secret[i : i + 4] for i in range(0, len(secret), 4))
    return render(
        request,
        "accounts/enroll.html",
        {"form": form, "qr_svg": qr_svg, "secret": grouped, "error": error, "forced": user.requires_2fa and not replacing,
         "replacing": replacing},
    )


@never_cache
@login_required
@sensitive_post_parameters()
def password_change_view(request):
    # تتغير بصمة الجلسة مع كلمة المرور فتنتهي الجلسات الأخرى، وتبقى الحالية وحدها.
    form = PasswordChange(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        user.must_change_password = False
        user.password_changed_at = timezone.now()
        user.save(update_fields=["must_change_password", "password_changed_at"])
        update_session_auth_hash(request, user)
        record(Action.SECURITY, user, message="تغيير كلمة المرور", actor=_actor(request, user))
        messages.success(request, "تم تغيير كلمة المرور.")
        return redirect(settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/password_change.html", {"form": form})


@never_cache
@login_required
@require_POST
def sessions_end_view(request):
    request.user.end_all_sessions(request)
    record(Action.SECURITY, request.user, message="إنهاء كل الجلسات الأخرى", actor=_actor(request, request.user))
    messages.success(request, "أُنهيت جلساتك على كل الأجهزة الأخرى.")
    return redirect("studio:profile")


@require_POST
def logout_view(request):
    if request.user.is_authenticated:
        record(Action.LOGOUT, request.user, message="خروج", actor=_actor(request, request.user))
    logout(request)
    return redirect("accounts:login")
