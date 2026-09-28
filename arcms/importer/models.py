from django.db import models
from django.utils import timezone


class LegacyRedirect(models.Model):
    """روابط الأرشيف القديم (من ووردبريس أو نظام سابق) تُحوَّل إلى الروابط الجديدة
    بتحويل دائم 301، فلا تضيع الروابط المنشورة في محركات البحث ومواقع التواصل."""

    old_path = models.CharField(max_length=500, unique=True)
    new_path = models.CharField(max_length=500)
    hits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self) -> str:
        return f"{self.old_path} → {self.new_path}"


class ImportRun(models.Model):
    source = models.CharField(max_length=20)
    filename = models.CharField(max_length=300)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    created = models.PositiveIntegerField(default=0)
    updated = models.PositiveIntegerField(default=0)
    skipped = models.PositiveIntegerField(default=0)
    errors = models.JSONField(default=list, blank=True)
    dry_run = models.BooleanField(default=False)

    class Meta:
        ordering = ["-started_at"]
