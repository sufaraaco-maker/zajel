"""صندوق المعلومات الآمن: بلاغات المصادر ومرفقاتها ورسائل المتابعة.

لا يُحفظ عن المرسل أي عنوان IP أو وكيل متصفح أو اسم ملف أصلي. النصوص
مشفّرة في القاعدة، والمرفقات مشفّرة كاملة بعد نزع بياناتها المخفية،
فتفريغ القاعدة أو النسخة الاحتياطية وحده لا يكشف شيئاً.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from arcms.core.fields import EncryptedTextField


class Tip(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "جديد"
        REVIEWING = "reviewing", "قيد التحقق"
        USED = "used", "بُني عليه عمل صحفي"
        CLOSED = "closed", "مغلق"
        SPAM = "spam", "مزعج"

    ref = models.CharField("المرجع", max_length=12, unique=True)
    code_hash = models.CharField(max_length=64, unique=True)
    subject = EncryptedTextField("الموضوع", blank=True)
    body = EncryptedTextField("المعلومة")
    contact = EncryptedTextField("وسيلة التواصل", blank=True)
    status = models.CharField("الحالة", max_length=12, choices=Status.choices, default=Status.NEW, db_index=True)
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="tips", verbose_name="المتابِع"
    )
    note = EncryptedTextField("ملاحظة داخلية", blank=True)
    unread = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-unread", "-updated_at", "-pk"]
        verbose_name = "بلاغ"
        verbose_name_plural = "البلاغات"

    def __str__(self) -> str:
        return self.ref


class TipMessage(models.Model):
    tip = models.ForeignKey(Tip, on_delete=models.CASCADE, related_name="messages")
    from_source = models.BooleanField(default=True)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    body = EncryptedTextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]


class TipAttachment(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image", "صورة"
        PDF = "pdf", "مستند PDF"

    tip = models.ForeignKey(Tip, on_delete=models.CASCADE, related_name="attachments")
    message = models.ForeignKey(TipMessage, null=True, blank=True, on_delete=models.CASCADE, related_name="attachments")
    kind = models.CharField(max_length=8, choices=Kind.choices)
    mime = models.CharField(max_length=40)
    ext = models.CharField(max_length=8)
    size = models.PositiveIntegerField(default=0)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    removed = models.JSONField("ما نُزع منها", default=list, blank=True)
    ciphertext = models.BinaryField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]

    @property
    def metadata_warning(self) -> bool:
        return self.kind == self.Kind.PDF
