"""حقول قاعدة بيانات مشفّرة للمعلومات الحساسة (ملاحظات المصادر، أسرار التحقق الثنائي).

يُشفَّر النص بـ Fernet (AES-128-CBC + HMAC-SHA256) بمفتاح من البيئة
ARCMS_FIELD_KEY. يمكن تدوير المفاتيح بوضع عدة مفاتيح مفصولة بفواصل:
الأول يُستخدم للتشفير، والبقية لفك التشفير فقط.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

PREFIX = "enc1:"


@lru_cache(maxsize=4)
def _fernet(keyspec: str) -> MultiFernet:
    keys = [k.strip() for k in keyspec.split(",") if k.strip()]
    if not keys:
        raise ImproperlyConfigured("ARCMS_FIELD_KEY غير مضبوط؛ لا يمكن تشفير الحقول الحساسة.")
    return MultiFernet([Fernet(k.encode()) for k in keys])


def encrypt_text(value: str) -> str:
    if value in (None, ""):
        return value or ""
    token = _fernet(settings.FIELD_ENCRYPTION_KEY).encrypt(value.encode("utf-8"))
    return PREFIX + token.decode("ascii")


def decrypt_text(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value or ""
    try:
        return _fernet(settings.FIELD_ENCRYPTION_KEY).decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        return "⚠ تعذّر فك التشفير (مفتاح غير مطابق)"


def encrypt_bytes(data: bytes) -> bytes:
    return _fernet(settings.FIELD_ENCRYPTION_KEY).encrypt(data)


def decrypt_bytes(token: bytes) -> bytes:
    """يرفع cryptography.fernet.InvalidToken إن لم يطابق المفتاح أو عُبث بالبيانات."""
    return _fernet(settings.FIELD_ENCRYPTION_KEY).decrypt(bytes(token))


class EncryptedTextField(models.TextField):
    """نص يُخزَّن مشفّراً ويُقرأ صريحاً داخل التطبيق فقط."""

    def from_db_value(self, value, expression, connection):
        return decrypt_text(value)

    def to_python(self, value):
        if isinstance(value, str) and value.startswith(PREFIX):
            return decrypt_text(value)
        return value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return encrypt_text(value) if value else value
