"""مفاتيح الأمان: تسجيلها وحذفها من «حسابي» (بكلمة المرور)، والدخول بها في خطوة التحقق الثنائي."""

from __future__ import annotations

import json
import secrets
import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.core.utils import client_ip

from . import webauthn
from .middleware import session_fully_verified
from .models import LoginAttempt, SecurityKey, User
from .views import PENDING_KEY, PENDING_TTL, _actor, _finish_login

REG_KEY = "arcms_wa_reg"
AUTH_KEY = "arcms_wa_auth"
CHALLENGE_TTL = 180
MAX_KEYS = 10


def _json_body(request) -> dict:
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def _password_ok(request, user: User, password: str) -> str:
    """يعيد نص الخطأ أو فارغاً. الأخطاء تُحتسب في قفل التخمين."""
    ip = client_ip(request)
    if LoginAttempt.is_locked(user.username, ip):
        return "تعددت المحاولات الفاشلة. أُقفل التأكيد مؤقتاً."
    if password and user.check_password(password):
        return ""
    LoginAttempt.objects.create(username=user.username, ip=ip, success=False, stage="password")
    record(Action.LOGIN_FAILED, user, message="كلمة مرور خاطئة عند إدارة مفاتيح الأمان", actor=_actor(request, user))
    return "كلمة المرور غير صحيحة."


def _fresh(entry: dict | None) -> bool:
    return bool(entry) and time.time() - entry.get("t", 0) <= CHALLENGE_TTL


# --- إدارة المفاتيح ---


@never_cache
@login_required
def keys_page(request):
    if not session_fully_verified(request):
        return redirect("accounts:verify")
    user: User = request.user
    if request.method == "POST":
        action = request.POST.get("action")
        error = _password_ok(request, user, request.POST.get("password", ""))
        if error:
            messages.error(request, error)
        elif action == "delete":
            key = get_object_or_404(SecurityKey, pk=request.POST.get("key"), user=user)
            key.delete()
            if not user.security_keys.exists() and user.keys_only:
                user.keys_only = False
                user.save(update_fields=["keys_only"])
            record(Action.TWO_FA, user, message=f"حذف مفتاح الأمان «{key.name}»", actor=_actor(request, user))
            messages.success(request, f"حُذف المفتاح «{key.name}».")
        elif action == "keys_only":
            enable = request.POST.get("enable") == "1"
            if enable and not user.security_keys.exists():
                messages.error(request, "سجّل مفتاحاً أولاً.")
            else:
                user.keys_only = enable
                user.save(update_fields=["keys_only"])
                record(Action.TWO_FA, user, actor=_actor(request, user),
                       message="الدخول بمفتاح الأمان فقط" if enable else "السماح برمز التطبيق مع مفتاح الأمان")
                messages.success(request, "حُفظ.")
        return redirect("accounts:keys")
    return render(request, "accounts/keys.html", {"keys": user.security_keys.all(), "max_keys": MAX_KEYS})


@never_cache
@login_required
@require_POST
def register_begin(request):
    if not session_fully_verified(request):
        return _error("أكمل التحقق الثنائي أولاً.", 403)
    user: User = request.user
    data = _json_body(request)
    error = _password_ok(request, user, str(data.get("password", "")))
    if error:
        return _error(error, 403)
    if user.security_keys.count() >= MAX_KEYS:
        return _error(f"الحد الأقصى {MAX_KEYS} مفاتيح.")
    name = " ".join(str(data.get("name", "")).split())[:60] or "مفتاح أمان"
    if not user.webauthn_handle:
        user.webauthn_handle = webauthn.b64url(secrets.token_bytes(32))
        user.save(update_fields=["webauthn_handle"])
    challenge = webauthn.new_challenge()
    request.session[REG_KEY] = {"c": challenge, "t": time.time(), "name": name}
    exclude = list(user.security_keys.values_list("credential_id", flat=True))
    return JsonResponse(webauthn.registration_options(user, user.webauthn_handle, challenge, exclude))


@never_cache
@login_required
@require_POST
def register_finish(request):
    if not session_fully_verified(request):
        return _error("أكمل التحقق الثنائي أولاً.", 403)
    user: User = request.user
    pending = request.session.pop(REG_KEY, None)
    if not _fresh(pending):
        return _error("انتهت مهلة التسجيل. أعد المحاولة.")
    data = _json_body(request)
    try:
        cred = webauthn.verify_registration(
            challenge=pending["c"], client_data_json=str(data.get("clientDataJSON", "")),
            attestation_object=str(data.get("attestationObject", "")),
        )
    except webauthn.WebAuthnError as exc:
        record(Action.SECURITY, user, message=f"فشل تسجيل مفتاح أمان: {exc}", actor=_actor(request, user))
        return _error(str(exc))
    if SecurityKey.objects.filter(credential_id=cred.credential_id).exists():
        return _error("هذا المفتاح مسجّل من قبل.")
    transports = [t for t in data.get("transports", []) if isinstance(t, str) and len(t) < 20][:6] \
        if isinstance(data.get("transports"), list) else []
    key = SecurityKey.objects.create(
        user=user, name=pending["name"], credential_id=cred.credential_id, public_key=cred.public_key,
        sign_count=cred.sign_count, aaguid=cred.aaguid, transports=transports,
    )
    record(Action.TWO_FA, user, message=f"تسجيل مفتاح الأمان «{key.name}»", actor=_actor(request, user))
    return JsonResponse({"ok": True, "name": key.name})


# --- الدخول بالمفتاح ---


def _pending_user(request) -> User | None:
    pending = request.session.get(PENDING_KEY)
    if not pending or time.time() - pending.get("t", 0) > PENDING_TTL:
        return None
    return User.objects.filter(pk=pending["id"], is_active=True).first()


@never_cache
@require_POST
def login_begin(request):
    user = _pending_user(request)
    if user is None:
        return _error("انتهت مهلة التحقق. أعد الدخول.", 403)
    ids = list(user.security_keys.values_list("credential_id", flat=True))
    if not ids:
        return _error("لا مفاتيح أمان لهذا الحساب.")
    challenge = webauthn.new_challenge()
    request.session[AUTH_KEY] = {"c": challenge, "t": time.time(), "u": user.pk}
    return JsonResponse(webauthn.authentication_options(challenge, ids))


@never_cache
@require_POST
def login_finish(request):
    user = _pending_user(request)
    pending = request.session.pop(AUTH_KEY, None)
    if user is None or not _fresh(pending) or pending.get("u") != user.pk:
        return _error("انتهت مهلة التحقق. أعد الدخول.", 403)
    ip = client_ip(request)
    if LoginAttempt.is_locked(user.username, ip):
        return _error("تعددت المحاولات الفاشلة. أُقفل الدخول مؤقتاً.", 403)
    data = _json_body(request)
    key = SecurityKey.objects.filter(user=user, credential_id=str(data.get("id", ""))[:1400]).first()
    try:
        if key is None:
            raise webauthn.WebAuthnError("هذا المفتاح غير مسجّل لهذا الحساب.")
        count = webauthn.verify_assertion(
            challenge=pending["c"], public_key=bytes(key.public_key), stored_count=key.sign_count,
            client_data_json=str(data.get("clientDataJSON", "")),
            authenticator_data=str(data.get("authenticatorData", "")), signature=str(data.get("signature", "")),
        )
    except webauthn.WebAuthnError as exc:
        LoginAttempt.objects.create(username=user.username, ip=ip, success=False, stage="otp")
        record(Action.LOGIN_FAILED, user, message=f"مفتاح أمان مرفوض: {exc}", actor=_actor(request, user))
        return _error(str(exc), 403)
    key.sign_count, key.last_used_at = count, timezone.now()
    key.save(update_fields=["sign_count", "last_used_at"])
    request.session.pop(PENDING_KEY, None)
    record(Action.TWO_FA, user, message=f"تحقق بمفتاح الأمان «{key.name}»", actor=_actor(request, user))
    response = _finish_login(request, user)
    return JsonResponse({"redirect": response["Location"]})
