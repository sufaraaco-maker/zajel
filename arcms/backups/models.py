from django.db import models
from django.utils import timezone


class BackupRecord(models.Model):
    class Status(models.TextChoices):
        RUNNING = "running", "قيد الإنشاء"
        OK = "ok", "مكتملة"
        FAILED = "failed", "فشلت"

    filename = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.RUNNING)
    size = models.BigIntegerField(default=0)
    sha256 = models.CharField(max_length=64, blank=True)
    key_fingerprint = models.CharField(max_length=32, blank=True)
    includes_media = models.BooleanField(default=True)
    trigger = models.CharField(max_length=20, default="manual")
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.filename
