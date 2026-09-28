from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone
from django.utils.crypto import salted_hmac

from arcms.core.fields import EncryptedTextField

from . import totp
from .roles import (
    DESK_SCOPED_ROLES,
    ROLE_RANK,
    ROLES_REQUIRING_2FA,
    Role,
    capabilities_for,
)


class User(AbstractUser):
    display_name = models.CharField("الاسم الظاهر", max_length=120, blank=True)
    role = models.CharField("الرتبة", max_length=20, choices=Role.choices, default=Role.REPORTER)
    phone = models.CharField("الهاتف (داخلي)", max_length=32, blank=True)

    totp_secret = EncryptedTextField("سر التحقق الثنائي", blank=True, default="")
    totp_confirmed_at = models.DateTimeField(null=True, blank=True)
    totp_last_counter = models.BigIntegerField(null=True, blank=True)
    recovery_codes = models.JSONField(default=list, blank=True)

    must_change_password = models.BooleanField("يجب تغيير كلمة المرور", default=False)
    password_changed_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    email_notifications = models.BooleanField(
        "تنبيهات البريد", default=True, help_text="بريد عند إرسال مادة للمراجعة أو إعادتها أو اعتمادها أو نشرها."
    )
    # يدخل في بصمة الجلسة: زيادته تُنهي كل الجلسات المفتوحة للحساب على كل الأجهزة.
    session_epoch = models.PositiveIntegerField(default=0, editable=False)

    objects = UserManager()

    class Meta:
        verbose_name = "مستخدم"
        verbose_name_plural = "المستخدمون"
        ordering = ["display_name", "username"]

    def __str__(self) -> str:
        return self.display_name or self.get_full_name() or self.username

    @property
    def name(self) -> str:
        return str(self)

    # --- الجلسات ---
    def _get_session_auth_hash(self, secret=None):
        if not self.session_epoch:
            return super()._get_session_auth_hash(secret=secret)
        key_salt = "arcms.accounts.User.session_auth_hash"
        return salted_hmac(key_salt, f"{self.password}:{self.session_epoch}", secret=secret, algorithm="sha256").hexdigest()

    def end_all_sessions(self, request=None) -> None:
        """يُبطل كل جلسات الحساب؛ ومع request تبقى الجلسة الحالية وحدها."""
        type(self).objects.filter(pk=self.pk).update(session_epoch=models.F("session_epoch") + 1)
        self.refresh_from_db(fields=["session_epoch"])
        if request is not None and request.user.pk == self.pk:
            from django.contrib.auth import update_session_auth_hash

            update_session_auth_hash(request, self)

    # --- الصلاحيات ---
    @property
    def capabilities(self) -> frozenset[str]:
        if not self.is_active:
            return frozenset()
        if self.is_superuser:
            return capabilities_for(Role.ADMIN)
        return capabilities_for(self.role)

    def can(self, cap: str) -> bool:
        return cap in self.capabilities

    @property
    def rank(self) -> int:
        return ROLE_RANK.get(Role.ADMIN if self.is_superuser else self.role, 0)

    @property
    def is_desk_scoped(self) -> bool:
        return self.role in DESK_SCOPED_ROLES and not self.is_superuser

    def desk_ids(self) -> set[int]:
        """الأقسام المسندة (مع أقسامها الفرعية). مجموعة فارغة = كل الأقسام."""
        if not self.is_desk_scoped:
            return set()
        ids = set(self.desks.values_list("id", flat=True))
        if ids:
            from arcms.content.models import Category

            children = Category.objects.filter(parent_id__in=ids).values_list("id", flat=True)
            ids |= set(children)
        return ids

    # --- التحقق الثنائي ---
    @property
    def has_2fa(self) -> bool:
        return bool(self.totp_secret and self.totp_confirmed_at)

    @property
    def requires_2fa(self) -> bool:
        if self.role in ROLES_REQUIRING_2FA or self.is_superuser:
            return True
        from arcms.core.models import SiteSettings

        return SiteSettings.load().require_2fa_all_staff

    def verify_totp(self, code: str) -> bool:
        counter = totp.verify(self.totp_secret, code, self.totp_last_counter)
        if counter is None:
            return False
        self.totp_last_counter = counter
        self.save(update_fields=["totp_last_counter"])
        return True

    def use_recovery_code(self, code: str) -> bool:
        digest = totp.hash_recovery_code(code)
        if digest in (self.recovery_codes or []):
            self.recovery_codes = [c for c in self.recovery_codes if c != digest]
            self.save(update_fields=["recovery_codes"])
            return True
        return False

    def reset_2fa(self) -> None:
        self.totp_secret = ""
        self.totp_confirmed_at = None
        self.totp_last_counter = None
        self.recovery_codes = []
        self.save(update_fields=["totp_secret", "totp_confirmed_at", "totp_last_counter", "recovery_codes"])
        # هاتف مفقود قد يكون في يد غيره: لا تبقى أي جلسة مفتوحة.
        self.end_all_sessions()


class LoginAttempt(models.Model):
    """سجل محاولات الدخول لقفل الحسابات أمام التخمين."""

    username = models.CharField(max_length=150, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    success = models.BooleanField(default=False)
    stage = models.CharField(max_length=12, default="password")  # password | otp
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]

    @classmethod
    def is_locked(cls, username: str, ip: str | None = None) -> bool:
        since = timezone.now() - timedelta(minutes=settings.ARCMS_LOGIN_LOCK_MINUTES)
        recent = cls.objects.filter(created_at__gte=since)
        by_user = recent.filter(username__iexact=username)
        last_ok = by_user.filter(success=True).order_by("-created_at").first()
        failures = by_user.filter(success=False)
        if last_ok:
            failures = failures.filter(created_at__gt=last_ok.created_at)
        if failures.count() >= settings.ARCMS_LOGIN_MAX_FAILURES:
            return True
        if ip:
            # حدّ أعلى لكل عنوان IP لمنع تجربة أسماء كثيرة من المصدر نفسه.
            ip_failures = recent.filter(ip=ip, success=False).count()
            if ip_failures >= settings.ARCMS_LOGIN_MAX_FAILURES * 4:
                return True
        return False
