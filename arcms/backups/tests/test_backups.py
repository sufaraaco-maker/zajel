import io
import os
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.test import override_settings

from arcms.backups import crypto
from arcms.backups.models import BackupRecord
from arcms.backups.service import BackupError, create_backup, inspect_backup, rotate
from arcms.core.testing import ArcmsTestCase, make_article


def roundtrip(data: bytes, **enc_kwargs) -> bytes:
    buf = io.BytesIO()
    w = crypto.EncryptingWriter(buf, **enc_kwargs)
    w.write(data)
    w.close()
    return buf.getvalue()


class CryptoTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.pub, self.priv = crypto.generate_keypair()

    def decrypt(self, blob: bytes, **kw) -> bytes:
        return crypto.DecryptingReader(io.BytesIO(blob), **kw).read()

    def test_public_key_roundtrip_multi_chunk(self):
        data = os.urandom(crypto.CHUNK * 2 + 12345)
        blob = roundtrip(data, public_key=self.pub)
        self.assertEqual(self.decrypt(blob, private_key=self.priv), data)
        self.assertNotIn(data[:64], blob)

    def test_exact_chunk_boundary_and_empty(self):
        for size in (0, crypto.CHUNK):
            data = b"x" * size
            self.assertEqual(self.decrypt(roundtrip(data, public_key=self.pub), private_key=self.priv), data)

    def test_passphrase_mode(self):
        blob = roundtrip(b"secret archive", passphrase="عبارة مرور طويلة", scrypt_log2=10)
        self.assertEqual(self.decrypt(blob, passphrase="عبارة مرور طويلة"), b"secret archive")
        with self.assertRaises(crypto.BackupCryptoError):
            self.decrypt(blob, passphrase="خطأ")

    def test_wrong_key_rejected(self):
        _, other_priv = crypto.generate_keypair()
        blob = roundtrip(b"data", public_key=self.pub)
        with self.assertRaises(crypto.BackupCryptoError):
            self.decrypt(blob, private_key=other_priv)

    def test_tampering_detected(self):
        blob = bytearray(roundtrip(b"a" * 5000, public_key=self.pub))
        blob[-20] ^= 0x01
        with self.assertRaises(crypto.BackupCryptoError):
            self.decrypt(bytes(blob), private_key=self.priv)

    def test_truncation_detected(self):
        data = os.urandom(crypto.CHUNK + 100)
        blob = roundtrip(data, public_key=self.pub)
        # حذف المقطع الأخير كاملاً: يجب ألا يُقبل الملف على أنه مكتمل
        header_len = 8 + 2 + 32 + 16 + 7
        size = int.from_bytes(blob[header_len : header_len + 4], "big")
        truncated = blob[: header_len + 4 + size]
        with self.assertRaises(crypto.BackupCryptoError):
            self.decrypt(truncated, private_key=self.priv)

    def test_header_bound(self):
        blob = bytearray(roundtrip(b"data", public_key=self.pub))
        blob[12] ^= 0xFF  # داخل المفتاح المؤقت في الترويسة
        with self.assertRaises(crypto.BackupCryptoError):
            self.decrypt(bytes(blob), private_key=self.priv)

    def test_fingerprint_stable(self):
        self.assertEqual(crypto.fingerprint(self.pub), crypto.fingerprint(self.pub))
        self.assertEqual(len(crypto.fingerprint(self.pub)), 16)


class BackupServiceTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp())
        self.pub, self.priv = crypto.generate_keypair()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        super().tearDown()

    def test_backup_and_verify(self):
        make_article("مادة في النسخة")
        media = Path(settings.MEDIA_ROOT)
        media.mkdir(parents=True, exist_ok=True)
        (media / "probe.txt").write_text("وسيط")
        with override_settings(ARCMS_BACKUP_DIR=self.tmp, ARCMS_BACKUP_PUBLIC_KEY=self.pub):
            rec = create_backup(trigger="اختبار")
        self.assertEqual(rec.status, BackupRecord.Status.OK)
        path = self.tmp / rec.filename
        self.assertTrue(path.exists())
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        raw = path.read_bytes()
        self.assertTrue(raw.startswith(crypto.MAGIC))
        self.assertNotIn("مادة في النسخة".encode(), raw)
        manifest = inspect_backup(path, private_key=self.priv)
        self.assertEqual(manifest["counts"]["articles"], 1)
        self.assertTrue(manifest["media"])
        self.assertGreaterEqual(manifest["members"], 3)

    def test_requires_key(self):
        with override_settings(ARCMS_BACKUP_DIR=self.tmp, ARCMS_BACKUP_PUBLIC_KEY=""):
            with self.assertRaises(BackupError):
                create_backup()

    def test_rotation(self):
        for n in range(5):
            (self.tmp / f"arcms-2026010{n}-000000.arcbak").write_bytes(b"x")
        with override_settings(ARCMS_BACKUP_DIR=self.tmp):
            self.assertEqual(rotate(keep=2), 3)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["arcms-20260103-000000.arcbak", "arcms-20260104-000000.arcbak"])

    def test_keygen_and_restore_verify_commands(self):
        key_file = self.tmp / "private.key"
        out = StringIO()
        call_command("arcms_backup_keygen", private_out=str(key_file), stdout=out)
        self.assertEqual(oct(key_file.stat().st_mode & 0o777), "0o600")
        pub = [line for line in out.getvalue().splitlines() if line.startswith("ARCMS_BACKUP_PUBLIC_KEY=")][0].split("=", 1)[1]
        with override_settings(ARCMS_BACKUP_DIR=self.tmp, ARCMS_BACKUP_PUBLIC_KEY=pub):
            call_command("arcms_backup", "--no-media", stdout=StringIO())
        backup = next(self.tmp.glob("*.arcbak"))
        out = StringIO()
        call_command("arcms_restore", str(backup), key=str(key_file), verify_only=True, stdout=out)
        self.assertIn("النسخة سليمة", out.getvalue())
        with self.assertRaises(SystemExit):
            call_command("arcms_backup_keygen", private_out=str(key_file), stdout=StringIO(), stderr=StringIO())
