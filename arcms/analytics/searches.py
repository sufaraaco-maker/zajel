"""سجل البحث المجمّع: يكشف للمحررين ما يطلبه القرّاء وما لا يجدونه (فجوات التغطية)."""

from __future__ import annotations

import re
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import F, Max, Sum
from django.utils import timezone

from arcms.arabic.normalize import normalize

from .collector import is_bot
from .models import SearchStat

RETENTION_DAYS = 90
_PRIVATE = re.compile(r"@|\d[\d\s\-]{6,}\d")  # بريد أو رقم هاتف أو هوية: لا يُسجَّل


def key(raw: str) -> str:
    text = normalize(raw or "")
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())[:120]


def record(raw: str, results: int, *, user_agent: str = "") -> bool:
    """يسجّل بحثاً في مجموع اليوم. يعيد False لما لا يُسجَّل (قصير، خاص، روبوت)."""
    q = key(raw)
    if len(q) < 2 or _PRIVATE.search(raw or "") or is_bot(user_agent):
        return False
    today = timezone.localdate()
    sample = " ".join((raw or "").split())[:120]
    updated = SearchStat.objects.filter(day=today, query=q).update(searches=F("searches") + 1, results=results)
    if not updated:
        try:
            with transaction.atomic():
                SearchStat.objects.create(day=today, query=q, sample=sample, searches=1, results=results)
        except IntegrityError:
            SearchStat.objects.filter(day=today, query=q).update(searches=F("searches") + 1, results=results)
    return True


def _window(days: int):
    return SearchStat.objects.filter(day__gte=timezone.localdate() - timedelta(days=days - 1))


def top(days: int = 7, limit: int = 20) -> list[dict]:
    return list(_window(days).values("query").annotate(n=Sum("searches"), sample=Max("sample"), results=Max("results"))
                .order_by("-n", "query")[:limit])


def unanswered(days: int = 7, limit: int = 20) -> list[dict]:
    """ما بحث عنه القرّاء ولم يجدوا له نتيجة طوال المدة."""
    return list(_window(days).values("query").annotate(n=Sum("searches"), sample=Max("sample"), results=Max("results"))
                .filter(results=0).order_by("-n", "query")[:limit])


def purge() -> int:
    deleted, _ = SearchStat.objects.filter(day__lt=timezone.localdate() - timedelta(days=RETENTION_DAYS)).delete()
    return deleted
