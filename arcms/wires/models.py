"""مكتب الوكالات: خلاصات RSS/Atom من وكالات الأنباء تصل إلى غرفة التحرير، ويُعتمد منها ما يلزم مسودةً."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class WireSource(models.Model):
    name = models.CharField("اسم المصدر", max_length=120, help_text="مثل: وكالة الأنباء الرسمية — الأخبار المحلية")
    feed_url = models.URLField("رابط الخلاصة (RSS أو Atom)", max_length=500)
    credit = models.CharField(
        "نص النسبة في المادة", max_length=120, blank=True,
        help_text="يُكتب في حقل «المصدر» للمادة المعتمدة. إن تُرك فارغاً يُستخدم اسم المصدر.",
    )
    category = models.ForeignKey(
        "content.Category", verbose_name="القسم الافتراضي للمسودة", null=True, blank=True, on_delete=models.SET_NULL,
    )
    is_active = models.BooleanField("مفعّل", default=True)
    poll_minutes = models.PositiveSmallIntegerField("الجلب كل (دقائق)", default=5)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    etag = models.CharField(max_length=200, blank=True)
    modified = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name = "مصدر وكالة"
        verbose_name_plural = "مصادر الوكالات"

    def __str__(self) -> str:
        return self.name

    @property
    def attribution(self) -> str:
        return self.credit or self.name

    def is_due(self, now=None) -> bool:
        now = now or timezone.now()
        if not self.last_polled_at:
            return True
        return (now - self.last_polled_at).total_seconds() >= max(1, self.poll_minutes) * 60 - 5

    def status_label(self) -> str:
        if not self.is_active:
            return "معطّل"
        if self.last_error:
            return f"خطأ: {self.last_error}"
        if self.last_ok_at:
            return "يعمل"
        return "لم يُجلب بعد"


class WireItem(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "جديد"
        ADOPTED = "adopted", "اعتُمد"
        IGNORED = "ignored", "تُجوهل"

    source = models.ForeignKey(WireSource, on_delete=models.CASCADE, related_name="items")
    guid_hash = models.CharField(max_length=64)
    title = models.CharField(max_length=500)
    summary = models.TextField(blank=True)
    body = models.TextField(blank=True)
    link = models.URLField(max_length=1000, blank=True)
    image_url = models.URLField(max_length=1000, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now, db_index=True)
    search_text = models.TextField(blank=True)
    is_alert = models.BooleanField(default=False, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NEW, db_index=True)
    article = models.ForeignKey("content.Article", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    handled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-published_at", "-id"]
        constraints = [models.UniqueConstraint(fields=["source", "guid_hash"], name="wire_item_unique_guid")]
        indexes = [models.Index(fields=["status", "-published_at"], name="wire_status_pub")]
        verbose_name = "مادة وكالة"
        verbose_name_plural = "مواد الوكالات"

    def __str__(self) -> str:
        return self.title[:100]

    @property
    def when(self):
        return self.published_at or self.fetched_at


class WireKeyword(models.Model):
    """كلمة تنبيه: أي مادة وكالة تحويها تُعلَّم «تنبيه» وتتصدر المكتب."""

    word = models.CharField("الكلمة أو العبارة", max_length=80, unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["word"]
        verbose_name = "كلمة تنبيه"
        verbose_name_plural = "كلمات التنبيه"

    def __str__(self) -> str:
        return self.word
