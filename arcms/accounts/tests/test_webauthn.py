"""مفاتيح الأمان مع «مفتاح برمجي» يوقّع كما يوقّع المفتاح الحقيقي (ES256 وEd25519 وRS256)."""

import hashlib
import json
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from django.conf import settings
from django.test import Client, override_settings
from django.urls import reverse

from arcms.accounts import cbor, totp, webauthn
from arcms.accounts.models import SecurityKey, User
from arcms.accounts.roles import Role
from arcms.audit.models import Action, AuditEntry
from arcms.core.testing import ArcmsTestCase, login, make_user

PASSWORD = "a-long-test-password"
ORIGIN = settings.SITE_URL


class SoftKey:
    """مفتاح أمان برمجي للاختبار."""

    def __init__(self, alg=webauthn.ES256, credential_id=b"cred-" + bytes(range(16)), rp="localhost"):
        self.alg, self.credential_id, self.rp, self.count = alg, credential_id, rp, 0
        if alg == webauthn.ES256:
            self.private = ec.generate_private_key(ec.SECP256R1())
            nums = self.private.public_key().public_numbers()
            self.cose = {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        elif alg == webauthn.EDDSA:
            self.private = ed25519.Ed25519PrivateKey.generate()
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

            raw = self.private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            self.cose = {1: 1, 3: -8, -1: 6, -2: raw}
        else:
            self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            nums = self.private.public_key().public_numbers()
            self.cose = {1: 3, 3: -257, -1: nums.n.to_bytes(256, "big"), -2: nums.e.to_bytes(3, "big")}

    def _auth_data(self, flags: int, attested: bool) -> bytes:
        data = hashlib.sha256(self.rp.encode()).digest() + bytes([flags]) + struct.pack(">I", self.count)
        if attested:
            data += bytes(16) + struct.pack(">H", len(self.credential_id)) + self.credential_id + cbor.encode(self.cose)
        return data

    @staticmethod
    def client_data(kind: str, challenge: str, origin: str = ORIGIN) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin}).encode()

    def create(self, options: dict, *, origin: str = ORIGIN, flags: int = 0x41) -> dict:
        client = self.client_data("webauthn.create", options["challenge"], origin)
        att = cbor.encode({"fmt": "none", "attStmt": {}, "authData": self._auth_data(flags, True)})
        return {"clientDataJSON": webauthn.b64url(client), "attestationObject": webauthn.b64url(att),
                "transports": ["usb"]}

    def get(self, options: dict, *, origin: str = ORIGIN, flags: int = 0x01, bump: int = 1, tamper: bool = False) -> dict:
        self.count += bump
        client = self.client_data("webauthn.get", options["challenge"], origin)
        auth = self._auth_data(flags, False)
        signed = auth + hashlib.sha256(client).digest()
        if self.alg == webauthn.ES256:
            sig = self.private.sign(signed, ec.ECDSA(hashes.SHA256()))
        elif self.alg == webauthn.EDDSA:
            sig = self.private.sign(signed)
        else:
            sig = self.private.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        if tamper:
            sig = sig[:-1] + bytes([sig[-1] ^ 1])
        return {"id": webauthn.b64url(self.credential_id), "clientDataJSON": webauthn.b64url(client),
                "authenticatorData": webauthn.b64url(auth), "signature": webauthn.b64url(sig)}


def post_json(client, name, data=None):
    return client.post(reverse(name), json.dumps(data or {}), content_type="application/json")


class CborTests(ArcmsTestCase):
    def test_roundtrip_and_strictness(self):
        value = {1: 2, -3: b"\x00\xff", "t": ["نص", True, None, -300, 70000]}
        self.assertEqual(cbor.decode(cbor.encode(value)), value)
        with self.assertRaises(cbor.CBORError):
            cbor.decode(cbor.encode(1) + b"\x00")
        with self.assertRaises(cbor.CBORError):
            cbor.decode(b"\x5a\xff\xff\xff\xff")  # بايتات بطول يتجاوز البيانات
        with self.assertRaises(cbor.CBORError):
            cbor.decode(b"\x81" * 40 + b"\x00")  # تداخل مفرط


class RegistrationTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("keyed", Role.EDITOR)
        login(self.client, self.user)

    def register(self, key: SoftKey, name="مفتاحي", **create_kwargs):
        resp = post_json(self.client, "accounts:key_register_begin", {"password": PASSWORD, "name": name})
        self.assertEqual(resp.status_code, 200, resp.content)
        options = resp.json()
        return post_json(self.client, "accounts:key_register_finish", key.create(options, **create_kwargs))

    def test_register_all_algorithms(self):
        for n, alg in enumerate((webauthn.ES256, webauthn.EDDSA, webauthn.RS256)):
            resp = self.register(SoftKey(alg, credential_id=bytes([n]) * 20), name=f"k{n}")
            self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.user.security_keys.count(), 3)
        self.user.refresh_from_db()
        self.assertTrue(self.user.webauthn_handle)
        self.assertTrue(AuditEntry.objects.filter(action=Action.TWO_FA, message__contains="تسجيل مفتاح").exists())

    def test_options_shape(self):
        options = post_json(self.client, "accounts:key_register_begin", {"password": PASSWORD}).json()
        self.assertEqual(options["rp"]["id"], "localhost")
        self.assertEqual(options["attestation"], "none")
        self.assertNotIn(str(self.user.pk), options["user"]["id"])  # معرّف عشوائي لا رقم الحساب
        self.assertEqual([p["alg"] for p in options["pubKeyCredParams"]], [-7, -8, -257])

    def test_password_required(self):
        resp = post_json(self.client, "accounts:key_register_begin", {"password": "wrong"})
        self.assertEqual(resp.status_code, 403)
        # بلا بدء صالح لا يُقبل إنهاء
        resp = post_json(self.client, "accounts:key_register_finish", SoftKey().create({"challenge": "x"}))
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(SecurityKey.objects.exists())

    def test_rejects_wrong_origin_rp_and_presence(self):
        self.assertEqual(self.register(SoftKey(), origin="https://evil.example").status_code, 400)
        self.assertEqual(self.register(SoftKey(rp="evil.example")).status_code, 400)
        self.assertEqual(self.register(SoftKey(), flags=0x40).status_code, 400)  # بلا لمس
        self.assertFalse(SecurityKey.objects.exists())

    def test_challenge_single_use_and_duplicate_key(self):
        key = SoftKey()
        options = post_json(self.client, "accounts:key_register_begin", {"password": PASSWORD}).json()
        payload = key.create(options)
        self.assertEqual(post_json(self.client, "accounts:key_register_finish", payload).status_code, 200)
        self.assertEqual(post_json(self.client, "accounts:key_register_finish", payload).status_code, 400)
        self.assertEqual(self.register(key).status_code, 400)
        self.assertEqual(SecurityKey.objects.count(), 1)

    def test_unverified_session_cannot_register(self):
        other = Client()
        other.force_login(self.user)  # كلمة مرور فقط، دون التحقق الثنائي
        resp = post_json(other, "accounts:key_register_begin", {"password": PASSWORD})
        self.assertIn(resp.status_code, (302, 403))  # الوسيط يعيده إلى التحقق قبل أن يصل إلى العرض
        self.assertFalse(SecurityKey.objects.exists())

    def test_delete_and_keys_only_need_password(self):
        self.register(SoftKey())
        key = SecurityKey.objects.get()
        self.client.post(reverse("accounts:keys"), {"action": "keys_only", "enable": "1", "password": "wrong"})
        self.user.refresh_from_db()
        self.assertFalse(self.user.keys_only)
        self.client.post(reverse("accounts:keys"), {"action": "keys_only", "enable": "1", "password": PASSWORD})
        self.user.refresh_from_db()
        self.assertTrue(self.user.keys_only)
        self.client.post(reverse("accounts:keys"), {"action": "delete", "key": key.pk, "password": "wrong"})
        self.assertTrue(SecurityKey.objects.exists())
        self.client.post(reverse("accounts:keys"), {"action": "delete", "key": key.pk, "password": PASSWORD})
        self.assertFalse(SecurityKey.objects.exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.keys_only)  # لا يبقى الحساب مقفلاً بلا مفاتيح
        self.assertContains(self.client.get(reverse("accounts:keys")), "إضافة مفتاح")


class LoginTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_user("signer", Role.EDITOR)
        self.key = SoftKey()
        c = Client()
        login(c, self.user)
        options = post_json(c, "accounts:key_register_begin", {"password": PASSWORD}).json()
        self.assertEqual(post_json(c, "accounts:key_register_finish", self.key.create(options)).status_code, 200)

    def password_step(self):
        self.client.post(reverse("accounts:login"), {"username": "signer", "password": PASSWORD})
        page = self.client.get(reverse("accounts:verify"))
        self.assertContains(page, "ادخل بمفتاح الأمان")
        return post_json(self.client, "accounts:key_login_begin").json()

    def test_login_with_key(self):
        options = self.password_step()
        self.assertEqual(options["allowCredentials"][0]["id"], webauthn.b64url(self.key.credential_id))
        resp = post_json(self.client, "accounts:key_login_finish", self.key.get(options))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["redirect"], reverse("studio:home"))
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)
        stored = SecurityKey.objects.get()
        self.assertEqual(stored.sign_count, 1)
        self.assertIsNotNone(stored.last_used_at)

    def test_bad_signature_origin_and_replay(self):
        options = self.password_step()
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", self.key.get(options, tamper=True)).status_code, 403)
        # التحدي يُستهلك بعد أي محاولة
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", self.key.get(options)).status_code, 403)
        options = post_json(self.client, "accounts:key_login_begin").json()
        phished = self.key.get(options, origin="https://arcms-login.example")
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", phished).status_code, 403)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 302)

    def test_cloned_key_counter(self):
        options = self.password_step()
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", self.key.get(options, bump=5)).status_code, 200)
        self.client.post(reverse("accounts:logout"))
        options = self.password_step()
        stale = self.key.get(options, bump=-3)  # نسخة بعدّاد متأخر
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", stale).status_code, 403)
        self.assertTrue(AuditEntry.objects.filter(message__contains="مستنسخاً").exists())

    def test_other_users_key_rejected(self):
        intruder = SoftKey(credential_id=b"intruder-key-000")
        options = self.password_step()
        self.assertEqual(post_json(self.client, "accounts:key_login_finish", intruder.get(options)).status_code, 403)

    def test_key_endpoints_need_pending_login(self):
        self.assertEqual(post_json(self.client, "accounts:key_login_begin").status_code, 403)

    def test_keys_only_blocks_app_code_but_not_recovery(self):
        codes = totp.generate_recovery_codes()
        self.user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
        self.user.keys_only = True
        self.user.save()
        self.client.post(reverse("accounts:login"), {"username": "signer", "password": PASSWORD})
        resp = self.client.post(reverse("accounts:verify"), {"code": totp.totp(self.user.totp_secret)})
        self.assertContains(resp, "الرمز غير صحيح")
        resp = self.client.post(reverse("accounts:verify"), {"code": codes[0]})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.client.get(reverse("studio:home")).status_code, 200)

    def test_admin_reset_removes_keys(self):
        admin = make_user("root", Role.ADMIN)
        login(self.client, admin)
        self.client.post(reverse("studio:user_reset_2fa", args=[self.user.pk]))
        self.assertFalse(SecurityKey.objects.filter(user=self.user).exists())
        self.user.refresh_from_db()
        self.assertFalse(self.user.keys_only)

    @override_settings(SITE_URL="https://news.example.org", ARCMS_WEBAUTHN_ORIGINS=("https://studio.example.org",))
    def test_rp_id_and_origins_from_settings(self):
        self.assertEqual(webauthn.rp_id(), "news.example.org")
        self.assertEqual(webauthn.expected_origins(), {"https://news.example.org", "https://studio.example.org"})


class UnitTests(ArcmsTestCase):
    def test_short_rsa_and_unknown_alg_rejected(self):
        weak = rsa.generate_private_key(public_exponent=65537, key_size=1024).public_key().public_numbers()
        with self.assertRaises(webauthn.WebAuthnError):
            webauthn.load_public_key(cbor.encode({1: 3, 3: -257, -1: weak.n.to_bytes(128, "big"), -2: b"\x01\x00\x01"}))
        with self.assertRaises(webauthn.WebAuthnError):
            webauthn.load_public_key(cbor.encode({1: 2, 3: -36, -1: 3, -2: b"x", -3: b"y"}))

    def test_user_model_helper(self):
        user = User.objects.create_user("plain", password=PASSWORD)
        self.assertFalse(user.uses_keys_only)
        user.keys_only = True
        self.assertFalse(user.uses_keys_only)  # بلا مفاتيح لا يُقفل الحساب
