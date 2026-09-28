"""تشفير النسخ الاحتياطية تدفّقياً.

الوضع المفضَّل: مفتاح عام (X25519). الخادم يحمل المفتاح العام فقط، فيستطيع
إنشاء نسخ مشفّرة لكنه لا يستطيع فكّها؛ المفتاح الخاص يبقى مع مدير النظام
خارج الخادم (حاسوب غير متصل أو خزنة). لو اختُرق الخادم أو سُرقت أقراصه
فالنسخ القديمة تبقى مغلقة.

البنية: ترويسة ثم مقاطع AES-256-GCM بحجم 1 ميغابايت، لكل مقطع رقم تسلسلي
وعلامة «الأخير» داخل الـ nonce (بناء STREAM)، والترويسة كلها بيانات موثَّقة
(AAD). أي عبث أو اقتطاع أو إعادة ترتيب يُكتشف عند الفك.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ARCMSBK1"
MODE_X25519 = 1
MODE_PASSPHRASE = 2
CHUNK = 1024 * 1024
PUB_PREFIX = "arcms-backup-pub:"
PRIV_PREFIX = "ARCMS-BACKUP-PRIVATE-KEY:"


class BackupCryptoError(Exception):
    pass


# --- المفاتيح ---


def generate_keypair() -> tuple[str, str]:
    priv = X25519PrivateKey.generate()
    raw_priv = priv.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    raw_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return PUB_PREFIX + base64.b64encode(raw_pub).decode(), PRIV_PREFIX + base64.b64encode(raw_priv).decode()


def load_public(text: str) -> X25519PublicKey:
    text = (text or "").strip()
    if not text.startswith(PUB_PREFIX):
        raise BackupCryptoError("صيغة المفتاح العام غير صحيحة (يجب أن يبدأ بـ arcms-backup-pub:)")
    return X25519PublicKey.from_public_bytes(base64.b64decode(text[len(PUB_PREFIX):]))


def load_private(text: str) -> X25519PrivateKey:
    text = (text or "").strip()
    if not text.startswith(PRIV_PREFIX):
        raise BackupCryptoError("صيغة المفتاح الخاص غير صحيحة.")
    return X25519PrivateKey.from_private_bytes(base64.b64decode(text[len(PRIV_PREFIX):]))


def fingerprint(public_text: str) -> str:
    raw = base64.b64decode(public_text.strip()[len(PUB_PREFIX):])
    return hashlib.sha256(raw).hexdigest()[:16]


def _hkdf(shared: bytes, salt: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(shared)


def _scrypt(passphrase: str, salt: bytes, n_log2: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**n_log2, r=8, p=1).derive(passphrase.encode("utf-8"))


def _nonce(prefix: bytes, counter: int, last: bool) -> bytes:
    return prefix + struct.pack(">IB", counter, 1 if last else 0)


# --- الكتابة ---


class EncryptingWriter(io.RawIOBase):
    """ملف قابل للكتابة يشفّر ما يُكتب فيه ويمرره إلى ملف الوجهة."""

    def __init__(self, dest, *, public_key: str | None = None, passphrase: str | None = None, scrypt_log2: int = 17):
        super().__init__()
        self.dest = dest
        salt = os.urandom(16)
        prefix = os.urandom(7)
        if public_key:
            recipient = load_public(public_key)
            eph = X25519PrivateKey.generate()
            eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            rec_pub = recipient.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            key = _hkdf(eph.exchange(recipient), salt, b"arcms-backup-v1" + eph_pub + rec_pub)
            header = MAGIC + bytes([1, MODE_X25519]) + eph_pub + salt + prefix
        elif passphrase:
            key = _scrypt(passphrase, salt, scrypt_log2)
            header = MAGIC + bytes([1, MODE_PASSPHRASE, scrypt_log2]) + salt + prefix
        else:
            raise BackupCryptoError("يلزم مفتاح عام أو عبارة مرور لتشفير النسخة.")
        self.aead = AESGCM(key)
        self.header = header
        self.prefix = prefix
        self.counter = 0
        self.buffer = bytearray()
        self.bytes_in = 0
        dest.write(header)

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self.buffer.extend(data)
        self.bytes_in += len(data)
        while len(self.buffer) > CHUNK:
            self._emit(bytes(self.buffer[:CHUNK]), last=False)
            del self.buffer[:CHUNK]
        return len(data)

    def _emit(self, plain: bytes, last: bool) -> None:
        ct = self.aead.encrypt(_nonce(self.prefix, self.counter, last), plain, self.header)
        self.dest.write(struct.pack(">I", len(ct)) + ct)
        self.counter += 1

    def close(self) -> None:
        if not self.closed:
            self._emit(bytes(self.buffer), last=True)
            self.buffer.clear()
            self.dest.flush()
        super().close()


# --- القراءة ---


class DecryptingReader(io.RawIOBase):
    def __init__(self, src, *, private_key: str | None = None, passphrase: str | None = None):
        super().__init__()
        self.src = src
        magic = src.read(8)
        if magic != MAGIC:
            raise BackupCryptoError("هذا ليس ملف نسخة احتياطية من arcms.")
        version, mode = src.read(2)
        if version != 1:
            raise BackupCryptoError("إصدار نسخة غير مدعوم.")
        if mode == MODE_X25519:
            eph_pub = src.read(32)
            salt = src.read(16)
            prefix = src.read(7)
            if not private_key:
                raise BackupCryptoError("هذه النسخة مشفّرة بمفتاح عام؛ يلزم ملف المفتاح الخاص لفكّها.")
            priv = load_private(private_key)
            rec_pub = priv.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            shared = priv.exchange(X25519PublicKey.from_public_bytes(eph_pub))
            key = _hkdf(shared, salt, b"arcms-backup-v1" + eph_pub + rec_pub)
            self.header = MAGIC + bytes([version, mode]) + eph_pub + salt + prefix
        elif mode == MODE_PASSPHRASE:
            n_log2 = src.read(1)[0]
            if not 1 <= n_log2 <= 20:  # ملف مصنوع قد يطلب كلفة تستهلك ذاكرة الخادم كلها
                raise BackupCryptoError("معامل اشتقاق المفتاح في الترويسة خارج الحدود المقبولة.")
            salt = src.read(16)
            prefix = src.read(7)
            if not passphrase:
                raise BackupCryptoError("هذه النسخة مشفّرة بعبارة مرور.")
            key = _scrypt(passphrase, salt, n_log2)
            self.header = MAGIC + bytes([version, mode, n_log2]) + salt + prefix
        else:
            raise BackupCryptoError("نمط تشفير غير معروف.")
        self.aead = AESGCM(key)
        self.prefix = prefix
        self.counter = 0
        self.pending = b""
        self.finished = False

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> bytes:
        size_raw = self.src.read(4)
        if len(size_raw) < 4:
            raise BackupCryptoError("الملف مقتطَع: لم يُعثر على المقطع الأخير.")
        (size,) = struct.unpack(">I", size_raw)
        ct = self.src.read(size)
        if len(ct) != size:
            raise BackupCryptoError("الملف مقتطَع.")
        # نحاول أولاً على أنه مقطع وسيط، ثم على أنه الأخير.
        for last in (False, True):
            try:
                plain = self.aead.decrypt(_nonce(self.prefix, self.counter, last), ct, self.header)
            except Exception:  # noqa: BLE001
                continue
            self.counter += 1
            if last:
                self.finished = True
                if self.src.read(1):
                    raise BackupCryptoError("بيانات زائدة بعد المقطع الأخير.")
            return plain
        raise BackupCryptoError("فشل التحقق: المفتاح خاطئ أو الملف معدَّل.")

    def readinto(self, b) -> int:
        while not self.pending and not self.finished:
            self.pending = self._next_chunk()
        n = min(len(b), len(self.pending))
        b[:n] = self.pending[:n]
        self.pending = self.pending[n:]
        return n
