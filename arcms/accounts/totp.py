"""كلمات المرور لمرة واحدة المبنية على الوقت (RFC 6238) ورموز الاسترداد.

تعمل مع أي تطبيق مصادقة (Google Authenticator، Aegis، FreeOTP، 1Password).
لا نعتمد على الرسائل النصية لأنها عرضة للاختراق وتكشف رقم هاتف الصحفي.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

DIGITS = 6
PERIOD = 30
WINDOW = 1  # نقبل الرمز السابق واللاحق لتفادي فروق الساعة


def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**digits)).zfill(digits)


def current_counter(at: float | None = None) -> int:
    return int((at if at is not None else time.time()) // PERIOD)


def totp(secret: str, at: float | None = None) -> str:
    return hotp(secret, current_counter(at))


def verify(secret: str, code: str, last_counter: int | None = None, at: float | None = None) -> int | None:
    """يعيد رقم العدّاد المطابق أو None. يرفض إعادة استخدام رمز سبق قبوله."""
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    code = code.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))
    if len(code) != DIGITS or not secret:
        return None
    try:
        _key(secret)
    except (ValueError, binascii.Error):
        # سر تالف أو لم يُفك تشفيره (مفتاح الحقول تغيّر): نرفض الرمز بدل أن نتعطل.
        return None
    now = current_counter(at)
    for counter in range(now - WINDOW, now + WINDOW + 1):
        if last_counter is not None and counter <= last_counter:
            continue
        if hmac.compare_digest(hotp(secret, counter), code):
            return counter
    return None


def provisioning_uri(secret: str, account: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DIGITS}&period={PERIOD}"
    )


# --- رموز الاسترداد ---
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_recovery_codes(n: int = 10) -> list[str]:
    codes = []
    for _ in range(n):
        raw = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def _clean_code(code: str) -> str:
    return "".join(ch for ch in code.upper() if ch.isalnum())


def hash_recovery_code(code: str) -> str:
    """تجزئة بطيئة بملح لكل رمز، فلا تُكسر رموز الاسترداد جماعياً إن تسرّبت القاعدة."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(_clean_code(code).encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"s1${salt.hex()}${digest.hex()}"


def recovery_code_matches(code: str, stored: str) -> bool:
    clean = _clean_code(code).encode()
    if stored.startswith("s1$"):
        try:
            _, salt, digest = stored.split("$")
            candidate = hashlib.scrypt(clean, salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
        except ValueError:
            return False
        return hmac.compare_digest(candidate.hex(), digest)
    return hmac.compare_digest(hashlib.sha256(clean).hexdigest(), stored)  # صيغة الإصدارات الأولى
