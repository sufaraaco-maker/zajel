"""التوزيع التلقائي: القنوات، سجل الإرسال، والمشتركون.

أسرار القنوات (رمز البوت، رمز واتساب، مفتاح VAPID) في متغيرات البيئة فقط؛
هنا الإعدادات غير السرية التي يضبطها المحرر من اللوحة.
"""

from __future__ import annotations

import secrets

from django.db import models
from django.utils import timezone


class Channel(models.TextChoices):
    TELEGRAM = "telegram", "تيليجرام"
    WHATSAPP = "whatsapp", "واتساب"
    PUSH = "push", "إشعارات المتصفح"
    NEWSLETTER = "newsletter", "النشرة البريدية"
    FACEBOOK = "facebook", "صفحة فيسبوك"
    X = "x", "إكس"


class ChannelConfig(models.Model):
    channel = models.CharField("القناة", max_length=12, choices=Channel.choices, unique=True)
    enabled = models.BooleanField("مفعّلة", default=False)
    auto_kinds = models.JSONField(
        "أنواع المواد التي تُرسل تلقائياً",
        default=list,
        blank=True,
        help_text="فارغة = كل الأنواع (حسب خيارات كل مادة).",
    )
    breaking_only = models.BooleanField("العاجل فقط", default=False)
    # تيليجرام
    telegram_chat_ids = models.CharField(
        "معرّفات القنوات", max_length=500, blank=True, help_text="مثل ‎@my_channel‎ أو ‎-100123…‎ مفصولة بفواصل"
    )
    with_image = models.BooleanField("إرسال الصورة مع الخبر", default=True)
    silent_hours = models.CharField(
        "ساعات الإرسال الصامت", max_length=20, blank=True, help_text="مثل 0-6 : يُرسل بلا صوت تنبيه في هذه الساعات"
    )
    # واتساب
    whatsapp_template = models.CharField(
        "اسم قالب واتساب المعتمد", max_length=80, blank=True, help_text="قالب بمتغيرين: العنوان والرابط"
    )
    whatsapp_language = models.CharField("لغة القالب", max_length=10, default="ar")
    # قالب النص
    message_template = models.TextField(
        "قالب الرسالة",
        blank=True,
        default="{kicker}<b>{title}</b>\n\n{summary}\n\n{url}",
        help_text="المتغيرات: {title} {summary} {url} {kicker} {category}",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "إعداد قناة"
        verbose_name_plural = "إعدادات القنوات"

    def __str__(self) -> str:
        return self.get_channel_display()

    @classmethod
    def get(cls, channel: str) -> "ChannelConfig":
        obj, _ = cls.objects.get_or_create(channel=channel)
        return obj

    def chat_ids(self) -> list[str]:
        return [c.strip() for c in self.telegram_chat_ids.split(",") if c.strip()]

    def accepts(self, kind: str, is_breaking: bool) -> bool:
        if not self.enabled:
            return False
        if self.breaking_only and not is_breaking:
            return False
        return not self.auto_kinds or kind in self.auto_kinds

    def is_silent_now(self) -> bool:
        if "-" not in (self.silent_hours or ""):
            return False
        try:
            start, end = (int(x) for x in self.silent_hours.split("-", 1))
        except ValueError:
            return False
        hour = timezone.localtime().hour
        return start <= hour < end if start <= end else hour >= start or hour < end


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "في الطابور"
        SENT = "sent", "أُرسلت"
        FAILED = "failed", "فشلت"
        SKIPPED = "skipped", "تُخطّيت"

    channel = models.CharField(max_length=12, choices=Channel.choices, db_index=True)
    article = models.ForeignKey("content.Article", null=True, blank=True, on_delete=models.CASCADE, related_name="deliveries")
    breaking = models.ForeignKey("content.BreakingNews", null=True, blank=True, on_delete=models.CASCADE, related_name="deliveries")
    target = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    recipients = models.PositiveIntegerField(default=0)
    failures = models.PositiveIntegerField(default=0)
    external_id = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_channel_display()} → {self.target}"


class NewsletterSubscriber(models.Model):
    email = models.EmailField(unique=True)
    token = models.CharField(max_length=48, unique=True, editable=False)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    unsubscribed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_active(self) -> bool:
        return bool(self.confirmed_at and not self.unsubscribed_at)


class NewsletterIssue(models.Model):
    date = models.DateField(unique=True)
    subject = models.CharField(max_length=200)
    html = models.TextField()
    text = models.TextField()
    article_ids = models.JSONField(default=list)
    sent_count = models.PositiveIntegerField(default=0)
    failed_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]


class PushSubscription(models.Model):
    endpoint = models.URLField(max_length=700, unique=True)
    p256dh = models.CharField(max_length=200)
    auth = models.CharField(max_length=100)
    created_at = models.DateTimeField(default=timezone.now)
    last_success_at = models.DateTimeField(null=True, blank=True)
    failures = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    topics = models.JSONField(default=list, blank=True)


class WhatsAppSubscriber(models.Model):
    phone = models.CharField("رقم الهاتف الدولي", max_length=20, unique=True, help_text="بصيغة 9705xxxxxxxx دون +")
    name = models.CharField(max_length=80, blank=True)
    opted_in_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)
    source = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["-opted_in_at"]
