"""خطة التغطية: مهام يكلّف بها رؤساء الأقسام والمحررون المناوبون الطاقم، بموعد وأولوية، وتتبع
مرحلتها من المادة المرتبطة بها تلقائياً (قيد العمل ← سُلّمت ← نُشرت)."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone

from arcms.core.fields import EncryptedTextField


class Assignment(models.Model):
    class Status(models.TextChoices):
        IDEA = "idea", "فكرة"
        ASSIGNED = "assigned", "مكلَّفة"
        WORKING = "working", "قيد العمل"
        FILED = "filed", "سُلّمت"
        DONE = "done", "نُشرت"
        DROPPED = "dropped", "أُلغيت"

    class Priority(models.IntegerChoices):
        NORMAL = 0, "عادية"
        HIGH = 1, "مهمة"
        URGENT = 2, "عاجلة"

    title = models.CharField("الموضوع", max_length=200)
    brief = EncryptedTextField(
        "التوجيه", blank=True,
        help_text="الزاوية المطلوبة، والمصادر المقترحة، وما يلزم من صور أو فيديو. مشفّر في القاعدة ولا يظهر للجمهور.",
    )
    category = models.ForeignKey("content.Category", verbose_name="القسم", null=True, blank=True, on_delete=models.SET_NULL)
    kind = models.CharField("نوع المادة", max_length=20, default="news")
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="المكلَّف", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="assignments",
    )
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    due_at = models.DateTimeField("الموعد النهائي", null=True, blank=True)
    priority = models.SmallIntegerField("الأولوية", choices=Priority.choices, default=Priority.NORMAL)
    status = models.CharField("المرحلة", max_length=10, choices=Status.choices, default=Status.IDEA, db_index=True)
    article = models.ForeignKey(
        "content.Article", verbose_name="المادة", null=True, blank=True, on_delete=models.SET_NULL, related_name="assignments",
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    OPEN = (Status.IDEA, Status.ASSIGNED, Status.WORKING, Status.FILED)

    class Meta:
        ordering = ["due_at", "-priority", "created_at"]
        verbose_name = "مهمة تغطية"
        verbose_name_plural = "خطة التغطية"

    def __str__(self) -> str:
        return self.title

    @property
    def is_overdue(self) -> bool:
        return bool(self.due_at and self.status in self.OPEN and self.due_at < timezone.now())

    @staticmethod
    def status_for_article(article) -> str:
        from arcms.content.models import Status as ArticleStatus

        if article.status == ArticleStatus.PUBLISHED:
            return Assignment.Status.DONE
        if article.status in (ArticleStatus.IN_REVIEW, ArticleStatus.APPROVED, ArticleStatus.SCHEDULED):
            return Assignment.Status.FILED
        return Assignment.Status.WORKING
