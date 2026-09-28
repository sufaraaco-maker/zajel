"""سجل تدقيق غير قابل للتعديل ومتسلسل بالتجزئة.

كل قيد يحمل تجزئة SHA-256 لمحتواه مع تجزئة القيد السابق، فأي تعديل أو
حذف لقيد قديم مباشرة في قاعدة البيانات يكسر السلسلة ويظهر عند الفحص
(صفحة سجل التدقيق أو الأمر ‎manage.py arcms_audit_verify‎).
"""

from __future__ import annotations

import hashlib
import json

from django.conf import settings
from django.db import models
from django.utils import timezone


class Action(models.TextChoices):
    CREATE = "create", "إنشاء"
    UPDATE = "update", "تعديل"
    DELETE = "delete", "حذف"
    LOGIN = "login", "دخول"
    LOGIN_FAILED = "login_failed", "محاولة دخول فاشلة"
    LOGOUT = "logout", "خروج"
    TWO_FA = "2fa", "التحقق الثنائي"
    WORKFLOW = "workflow", "سير العمل"
    PUBLISH = "publish", "نشر"
    UNPUBLISH = "unpublish", "سحب"
    DISTRIBUTE = "distribute", "توزيع"
    SETTINGS = "settings", "إعدادات"
    BACKUP = "backup", "نسخ احتياطي"
    IMPORT = "import", "استيراد"
    SECURITY = "security", "أمان"


class AuditEntry(models.Model):
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_label = models.CharField(max_length=150, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices, db_index=True)
    object_type = models.CharField(max_length=60, blank=True, db_index=True)
    object_id = models.CharField(max_length=64, blank=True, db_index=True)
    object_repr = models.CharField(max_length=255, blank=True)
    message = models.CharField(max_length=500, blank=True)
    changes = models.JSONField(default=dict, blank=True)
    prev_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64, unique=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "قيد تدقيق"
        verbose_name_plural = "سجل التدقيق"

    def __str__(self) -> str:
        return f"{self.get_action_display()} {self.object_type}#{self.object_id}"

    def payload(self) -> dict:
        return {
            "created_at": self.created_at.isoformat(timespec="microseconds"),
            "actor": self.actor_id,
            "actor_label": self.actor_label,
            "ip": self.ip,
            "action": self.action,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "object_repr": self.object_repr,
            "message": self.message,
            "changes": self.changes,
        }

    def compute_hash(self) -> str:
        body = json.dumps(self.payload(), ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256((self.prev_hash + "|" + body).encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        if self.pk:
            raise PermissionError("قيود التدقيق لا تُعدَّل.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("قيود التدقيق لا تُحذف.")
