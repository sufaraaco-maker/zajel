"""استطلاعات القرّاء: سؤال وخيارات وعدّادات فقط. لا يُحفظ من صوّت ولا عنوانه؛ منع التكرار
بعلامة في متصفح القارئ وحدّ لكل عنوان في الساعة بمفاتيح مجزّأة لا تُخزَّن."""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class Poll(models.Model):
    question = models.CharField("السؤال", max_length=250)
    description = models.CharField("توضيح", max_length=300, blank=True)
    is_open = models.BooleanField("مفتوح للتصويت", default=True)
    closes_at = models.DateTimeField("يُغلق في", null=True, blank=True)
    show_results_before_vote = models.BooleanField(
        "إظهار النتائج قبل التصويت", default=False, help_text="افتراضياً تظهر النتائج بعد التصويت أو بعد الإغلاق.",
    )
    total_votes = models.PositiveIntegerField(default=0, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "استطلاع"
        verbose_name_plural = "الاستطلاعات"

    def __str__(self) -> str:
        return self.question

    @property
    def is_active(self) -> bool:
        return self.is_open and (self.closes_at is None or self.closes_at > timezone.now())

    @property
    def shortcode(self) -> str:
        return f"[poll:{self.pk}]"

    def results(self) -> list[dict]:
        total = self.total_votes or 0
        return [
            {"id": o.pk, "text": o.text, "votes": o.votes, "pct": round(o.votes * 100 / total) if total else 0}
            for o in self.options.all()
        ]


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name="options")
    text = models.CharField("الخيار", max_length=150)
    order = models.PositiveSmallIntegerField(default=0)
    votes = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "pk"]

    def __str__(self) -> str:
        return self.text
