"""منطق صندوق المعلومات: رموز المتابعة، ومعالجة المرفقات، والاحتفاظ."""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from arcms.content.imaging import ImageRejected, strip_and_normalize
from arcms.core.fields import decrypt_bytes, encrypt_bytes

from .models import Tip, TipAttachment, TipMessage

# بلا حروف ملتبسة (0/O، 1/I/L، U) كي يُنقل الرمز يدوياً دون خطأ.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"
CODE_LENGTH = 20  # نحو 98 بتاً: لا يُخمَّن
REF_ALPHABET = "ABCDEFGHJKMNPQRSTVWXYZ23456789"

MAX_FILES = 5
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_REQUEST_BYTES = 50 * 1024 * 1024
MAX_BODY_CHARS = 20000


class AttachmentRejected(ValueError):
    pass


def new_code() -> str:
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    return "-".join(raw[i : i + 5] for i in range(0, CODE_LENGTH, 5))


def normalize_code(code: str) -> str:
    digits = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
    return "".join(c for c in (code or "").translate(digits).upper() if c.isalnum())


def hash_code(code: str) -> str:
    # الرمز عشوائي بطول كافٍ، فالتجزئة المباشرة لا تُكسر بالتخمين.
    return hashlib.sha256(("arcms-tip:" + normalize_code(code)).encode()).hexdigest()


def find_tip(code: str) -> Tip | None:
    if len(normalize_code(code)) != CODE_LENGTH:
        return None
    return Tip.objects.filter(code_hash=hash_code(code)).first()


def _new_ref() -> str:
    while True:
        ref = "T-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))
        if not Tip.objects.filter(ref=ref).exists():
            return ref


def prepare_attachment(data: bytes) -> dict:
    """ينزع البيانات المخفية من الصور ويقبل PDF كما هو مع تنبيه. يعيد حقول المرفق."""
    if len(data) > MAX_FILE_BYTES:
        raise AttachmentRejected("حجم الملف يتجاوز 15 ميغابايت.")
    if data[:5] == b"%PDF-":
        return {"kind": TipAttachment.Kind.PDF, "mime": "application/pdf", "ext": "pdf", "content": data, "removed": []}
    try:
        clean = strip_and_normalize(data)
    except ImageRejected as exc:
        raise AttachmentRejected("نقبل الصور وملفات PDF فقط.") from exc
    return {
        "kind": TipAttachment.Kind.IMAGE,
        "mime": clean.mime,
        "ext": clean.ext,
        "content": clean.content,
        "removed": clean.removed,
        "width": clean.width,
        "height": clean.height,
    }


def _store(tip: Tip, prepared: list[dict], message: TipMessage | None = None) -> None:
    for item in prepared:
        content = item.pop("content")
        TipAttachment.objects.create(tip=tip, message=message, size=len(content), ciphertext=encrypt_bytes(content), **item)


def read_attachment(att: TipAttachment) -> bytes:
    return decrypt_bytes(att.ciphertext)


@transaction.atomic
def create_tip(*, body: str, subject: str = "", contact: str = "", files: list[bytes] = ()) -> tuple[Tip, str]:
    prepared = [prepare_attachment(f) for f in files]
    code = new_code()
    tip = Tip.objects.create(
        ref=_new_ref(), code_hash=hash_code(code), subject=subject[:200], body=body[:MAX_BODY_CHARS], contact=contact[:300]
    )
    _store(tip, prepared)
    return tip, code


@transaction.atomic
def add_source_message(tip: Tip, body: str, files: list[bytes] = ()) -> TipMessage:
    prepared = [prepare_attachment(f) for f in files]
    msg = TipMessage.objects.create(tip=tip, from_source=True, body=body[:MAX_BODY_CHARS])
    _store(tip, prepared, msg)
    tip.unread = True
    if tip.status == Tip.Status.CLOSED:
        tip.status = Tip.Status.REVIEWING
    tip.save(update_fields=["unread", "status", "updated_at"])
    return msg


def add_newsroom_reply(tip: Tip, user, body: str) -> TipMessage:
    msg = TipMessage.objects.create(tip=tip, from_source=False, author=user, body=body[:MAX_BODY_CHARS])
    Tip.objects.filter(pk=tip.pk).update(updated_at=timezone.now())
    return msg


def retention_days() -> int:
    return int(getattr(settings, "ARCMS_TIPS_RETENTION_DAYS", 90))


def purge_expired() -> int:
    """يحذف نهائياً البلاغات المغلقة أو المزعجة التي لم تتحرك منذ مدة الاحتفاظ."""
    cutoff = timezone.now() - timedelta(days=retention_days())
    qs = Tip.objects.filter(status__in=[Tip.Status.CLOSED, Tip.Status.SPAM], updated_at__lt=cutoff)
    count = qs.count()
    if count:
        qs.delete()
    return count
