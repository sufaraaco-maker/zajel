"""قياس الجمهور داخل الخادم نفسه، دون طرف ثالث ودون ملفات تعريف ارتباط.

لا نخزّن عنوان IP أبداً. الزائر يُمثَّل ببصمة يومية:
    SHA-256(ملح اليوم + IP + وكيل المتصفح)
والملح يُولَّد عشوائياً كل يوم ويُحذف بعد انقضائه، فتستحيل إعادة ربط
البصمة بالعنوان أو تتبّع القارئ عبر الأيام. هذا يحمي القرّاء أيضاً:
لا توجد سجلات تُطلب لاحقاً لمعرفة من قرأ ماذا.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class DailySalt(models.Model):
    day = models.DateField(unique=True)
    salt = models.CharField(max_length=64)


class PageView(models.Model):
    class Source(models.TextChoices):
        DIRECT = "direct", "مباشر"
        SEARCH = "search", "محركات البحث"
        SOCIAL = "social", "مواقع التواصل"
        MESSAGING = "messaging", "تطبيقات المراسلة"
        INTERNAL = "internal", "تصفح داخلي"
        REFERRAL = "referral", "مواقع أخرى"
        PUSH = "push", "الإشعارات"
        NEWSLETTER = "newsletter", "النشرة البريدية"

    class Device(models.TextChoices):
        MOBILE = "mobile", "هاتف"
        TABLET = "tablet", "لوحي"
        DESKTOP = "desktop", "حاسوب"

    ts = models.DateTimeField(default=timezone.now, db_index=True)
    path = models.CharField(max_length=300)
    article_id = models.BigIntegerField(null=True, blank=True, db_index=True)
    category_id = models.BigIntegerField(null=True, blank=True)
    visitor = models.CharField(max_length=16, db_index=True)
    source = models.CharField(max_length=12, choices=Source.choices, default=Source.DIRECT)
    referrer_host = models.CharField(max_length=120, blank=True)
    device = models.CharField(max_length=8, choices=Device.choices, default=Device.DESKTOP)

    class Meta:
        indexes = [models.Index(fields=["ts", "article_id"])]


class DailyArticleStat(models.Model):
    day = models.DateField(db_index=True)
    article = models.ForeignKey("content.Article", on_delete=models.CASCADE, related_name="daily_stats")
    views = models.PositiveIntegerField(default=0)
    visitors = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = [("day", "article")]


class DailySiteStat(models.Model):
    day = models.DateField(unique=True)
    views = models.PositiveIntegerField(default=0)
    visitors = models.PositiveIntegerField(default=0)
    by_source = models.JSONField(default=dict)
    by_device = models.JSONField(default=dict)
    by_referrer = models.JSONField(default=dict)
    by_category = models.JSONField(default=dict)
