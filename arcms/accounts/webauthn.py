"""مفاتيح الأمان ومفاتيح المرور (WebAuthn) عاملاً ثانياً لا يُصطاد: المتصفح لا يوقّع إلا لنطاق
الموقع الحقيقي، فصفحة دخول مزيفة لا تحصل على شيء تعيد استخدامه.

التحقق مكتوب بمكتبة cryptography وفق مواصفة W3C WebAuthn Level 2:
- التسجيل: clientDataJSON (النوع، التحدي، الأصل) ثم attestationObject بصيغة «none» (لا نطلب
  شهادة الصانع؛ لا نتتبع طراز الجهاز)، وبصمة النطاق، وعلَم حضور المستخدم، والمفتاح العام COSE.
- الدخول: الشيء نفسه، ثم التوقيع على authenticatorData‖SHA-256(clientDataJSON)، وعدّاد التوقيع
  لكشف المفاتيح المستنسخة.
الخوارزميات: ES256 وEd25519 وRS256.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import struct
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from django.conf import settings

from . import cbor

ES256, EDDSA, RS256 = -7, -8, -257
ALGORITHMS = (ES256, EDDSA, RS256)
FLAG_UP, FLAG_UV, FLAG_AT, FLAG_ED = 0x01, 0x04, 0x40, 0x80
TIMEOUT_MS = 120_000


class WebAuthnError(Exception):
    pass


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > 16384:
        raise WebAuthnError("قيمة غير صالحة.")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise WebAuthnError("ترميز غير صالح.") from exc


def rp_id() -> str:
    return settings.ARCMS_WEBAUTHN_RP_ID or (urlsplit(settings.SITE_URL).hostname or "localhost")


def expected_origins() -> set[str]:
    parts = urlsplit(settings.SITE_URL)
    origins = {f"{parts.scheme}://{parts.netloc}"}
    origins.update(settings.ARCMS_WEBAUTHN_ORIGINS)
    return origins


def new_challenge() -> str:
    return b64url(secrets.token_bytes(32))


# --- الخيارات التي يُمررها المتصفح إلى navigator.credentials ---


def registration_options(user, handle: str, challenge: str, exclude: list[str]) -> dict:
    return {
        "challenge": challenge,
        "rp": {"id": rp_id(), "name": _site_name()},
        "user": {"id": handle, "name": user.username, "displayName": user.name},
        "pubKeyCredParams": [{"type": "public-key", "alg": alg} for alg in ALGORITHMS],
        "timeout": TIMEOUT_MS,
        "attestation": "none",
        "authenticatorSelection": {"residentKey": "discouraged", "userVerification": "preferred"},
        "excludeCredentials": [{"type": "public-key", "id": cid} for cid in exclude],
    }


def authentication_options(challenge: str, credential_ids: list[str]) -> dict:
    return {
        "challenge": challenge,
        "rpId": rp_id(),
        "timeout": TIMEOUT_MS,
        "userVerification": "preferred",
        "allowCredentials": [{"type": "public-key", "id": cid} for cid in credential_ids],
    }


def _site_name() -> str:
    from arcms.core.models import SiteSettings

    try:
        return SiteSettings.load().name
    except Exception:  # noqa: BLE001 - قبل إنشاء الجداول
        return "arcms"


# --- التحقق ---


def _client_data(raw: bytes, kind: str, challenge: str) -> None:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise WebAuthnError("بيانات المتصفح غير صالحة.") from exc
    if data.get("type") != kind:
        raise WebAuthnError("نوع العملية غير متوقع.")
    if not secrets.compare_digest(str(data.get("challenge", "")), challenge):
        raise WebAuthnError("التحدي غير مطابق أو انتهت صلاحيته.")
    if data.get("origin") not in expected_origins():
        raise WebAuthnError("العملية لم تجرِ على نطاق الموقع.")
    if data.get("crossOrigin"):
        raise WebAuthnError("عملية من إطار خارجي مرفوضة.")


@dataclass
class AuthData:
    rp_id_hash: bytes
    flags: int
    sign_count: int
    aaguid: bytes = b""
    credential_id: bytes = b""
    public_key: bytes = b""


def parse_auth_data(data: bytes) -> AuthData:
    if len(data) < 37:
        raise WebAuthnError("بيانات المفتاح قصيرة.")
    parsed = AuthData(data[:32], data[32], struct.unpack(">I", data[33:37])[0])
    if parsed.flags & FLAG_AT:
        if len(data) < 55:
            raise WebAuthnError("بيانات المفتاح مقطوعة.")
        parsed.aaguid = data[37:53]
        length = struct.unpack(">H", data[53:55])[0]
        cid_end = 55 + length
        if length == 0 or length > 1023 or cid_end > len(data):
            raise WebAuthnError("معرّف المفتاح غير صالح.")
        parsed.credential_id = data[55:cid_end]
        try:
            _, key_end = cbor.decode_first(data, cid_end)
        except cbor.CBORError as exc:
            raise WebAuthnError("المفتاح العام غير صالح.") from exc
        parsed.public_key = data[cid_end:key_end]
        rest = data[key_end:]
        if rest and not parsed.flags & FLAG_ED:
            raise WebAuthnError("بيانات زائدة في استجابة المفتاح.")
    return parsed


def _check_rp_and_presence(auth: AuthData) -> None:
    if not secrets.compare_digest(auth.rp_id_hash, hashlib.sha256(rp_id().encode()).digest()):
        raise WebAuthnError("المفتاح سُجّل لنطاق آخر.")
    if not auth.flags & FLAG_UP:
        raise WebAuthnError("لم يُلمس المفتاح (حضور المستخدم مطلوب).")


def load_public_key(cose: bytes):
    """يحوّل مفتاح COSE إلى مفتاح cryptography مع خوارزميته."""
    try:
        key = cbor.decode(cose)
    except cbor.CBORError as exc:
        raise WebAuthnError("المفتاح العام غير صالح.") from exc
    if not isinstance(key, dict):
        raise WebAuthnError("المفتاح العام غير صالح.")
    kty, alg = key.get(1), key.get(3)
    try:
        if kty == 2 and alg == ES256 and key.get(-1) == 1:
            x, y = key[-2], key[-3]
            if len(x) != 32 or len(y) != 32:
                raise WebAuthnError("مفتاح ES256 غير صالح.")
            return ec.EllipticCurvePublicNumbers(int.from_bytes(x, "big"), int.from_bytes(y, "big"),
                                                 ec.SECP256R1()).public_key(), alg
        if kty == 1 and alg == EDDSA and key.get(-1) == 6:
            return ed25519.Ed25519PublicKey.from_public_bytes(key[-2]), alg
        if kty == 3 and alg == RS256:
            n, e = int.from_bytes(key[-1], "big"), int.from_bytes(key[-2], "big")
            if n.bit_length() < 2048:
                raise WebAuthnError("مفتاح RSA أقصر من 2048 بت.")
            return rsa.RSAPublicNumbers(e, n).public_key(), alg
    except (KeyError, TypeError, ValueError) as exc:
        raise WebAuthnError("المفتاح العام غير صالح.") from exc
    raise WebAuthnError("خوارزمية المفتاح غير مدعومة.")


def _verify_signature(public_key, alg: int, signature: bytes, signed: bytes) -> None:
    try:
        if alg == ES256:
            public_key.verify(signature, signed, ec.ECDSA(hashes.SHA256()))
        elif alg == EDDSA:
            public_key.verify(signature, signed)
        elif alg == RS256:
            public_key.verify(signature, signed, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise WebAuthnError("خوارزمية المفتاح غير مدعومة.")
    except InvalidSignature as exc:
        raise WebAuthnError("التوقيع غير صحيح.") from exc


@dataclass
class NewCredential:
    credential_id: str
    public_key: bytes
    sign_count: int
    aaguid: str
    user_verified: bool


def verify_registration(*, challenge: str, client_data_json: str, attestation_object: str) -> NewCredential:
    client_raw = unb64url(client_data_json)
    _client_data(client_raw, "webauthn.create", challenge)
    try:
        att = cbor.decode(unb64url(attestation_object))
    except cbor.CBORError as exc:
        raise WebAuthnError("استجابة المفتاح غير صالحة.") from exc
    if not isinstance(att, dict) or not isinstance(att.get("authData"), bytes):
        raise WebAuthnError("استجابة المفتاح غير صالحة.")
    # لا نطلب شهادة الصانع («none»)؛ أي صيغة أخرى تُقبل دون الاعتماد على شهادتها.
    auth = parse_auth_data(att["authData"])
    _check_rp_and_presence(auth)
    if not auth.flags & FLAG_AT or not auth.credential_id:
        raise WebAuthnError("الاستجابة لا تحمل مفتاحاً جديداً.")
    load_public_key(auth.public_key)  # يرفض الخوارزميات غير المدعومة الآن لا عند الدخول
    aaguid = auth.aaguid.hex()
    return NewCredential(
        credential_id=b64url(auth.credential_id),
        public_key=auth.public_key,
        sign_count=auth.sign_count,
        aaguid=f"{aaguid[:8]}-{aaguid[8:12]}-{aaguid[12:16]}-{aaguid[16:20]}-{aaguid[20:]}" if aaguid else "",
        user_verified=bool(auth.flags & FLAG_UV),
    )


def verify_assertion(*, challenge: str, public_key: bytes, stored_count: int, client_data_json: str,
                     authenticator_data: str, signature: str) -> int:
    """يعيد عدّاد التوقيع الجديد، أو يرفع WebAuthnError."""
    client_raw = unb64url(client_data_json)
    _client_data(client_raw, "webauthn.get", challenge)
    auth_raw = unb64url(authenticator_data)
    auth = parse_auth_data(auth_raw)
    _check_rp_and_presence(auth)
    key, alg = load_public_key(public_key)
    _verify_signature(key, alg, unb64url(signature), auth_raw + hashlib.sha256(client_raw).digest())
    if (auth.sign_count or stored_count) and auth.sign_count <= stored_count:
        raise WebAuthnError("عدّاد المفتاح لم يتقدم؛ قد يكون المفتاح مستنسخاً.")
    return auth.sign_count
