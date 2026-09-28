"""استعلامات لوحة القيادة و«الأكثر قراءة»."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, Sum
from django.utils import timezone

from .models import DailyArticleStat, DailySiteStat, PageView


def realtime(minutes: int = 5) -> dict:
    since = timezone.now() - timedelta(minutes=minutes)
    qs = PageView.objects.filter(ts__gte=since)
    top = list(
        qs.exclude(article_id__isnull=True)
        .values("article_id")
        .annotate(n=Count("visitor", distinct=True))
        .order_by("-n")[:5]
    )
    return {"active": qs.values("visitor").distinct().count(), "top": top}


def today_numbers() -> dict:
    now = timezone.now()
    start = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
    qs = PageView.objects.filter(ts__gte=start)
    yesterday_same = PageView.objects.filter(ts__gte=start - timedelta(days=1), ts__lt=now - timedelta(days=1))
    return {
        "views": qs.count(),
        "visitors": qs.values("visitor").distinct().count(),
        "views_yesterday_same_time": yesterday_same.count(),
    }


def series(days: int = 30) -> list[dict]:
    today = timezone.localdate()
    start = today - timedelta(days=days - 1)
    stats = {s.day: s for s in DailySiteStat.objects.filter(day__gte=start)}
    live = today_numbers()
    out = []
    for i in range(days):
        day = start + timedelta(days=i)
        if day == today:
            out.append({"day": day, "views": live["views"], "visitors": live["visitors"]})
        else:
            s = stats.get(day)
            out.append({"day": day, "views": s.views if s else 0, "visitors": s.visitors if s else 0})
    return out


def breakdown(days: int = 7) -> dict:
    start = timezone.localdate() - timedelta(days=days - 1)
    src, dev, ref, cat = Counter(), Counter(), Counter(), Counter()
    for s in DailySiteStat.objects.filter(day__gte=start):
        src.update(s.by_source)
        dev.update(s.by_device)
        ref.update(s.by_referrer)
        cat.update(s.by_category)
    return {"sources": src, "devices": dev, "referrers": ref, "categories": cat}


def top_articles(days: int = 1, limit: int = 10) -> list[tuple[int, int, int]]:
    """(رقم المادة، المشاهدات، الزوار). اليوم من السجل الخام، وما قبله من التجميع."""
    today = timezone.localdate()
    totals: Counter = Counter()
    visitors: Counter = Counter()
    start_today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    for r in (
        PageView.objects.filter(ts__gte=start_today)
        .exclude(article_id__isnull=True)
        .values("article_id")
        .annotate(v=Count("id"), u=Count("visitor", distinct=True))
    ):
        totals[r["article_id"]] += r["v"]
        visitors[r["article_id"]] += r["u"]
    if days > 1:
        for r in (
            DailyArticleStat.objects.filter(day__gte=today - timedelta(days=days - 1), day__lt=today)
            .values("article_id")
            .annotate(v=Sum("views"), u=Sum("visitors"))
        ):
            totals[r["article_id"]] += r["v"]
            visitors[r["article_id"]] += r["u"]
    return [(aid, n, visitors[aid]) for aid, n in totals.most_common(limit)]


def most_read_ids(days: int = 2, limit: int = 8) -> list[int]:
    key = f"arcms:most-read:{days}:{limit}"
    ids = cache.get(key)
    if ids is None:
        ids = [aid for aid, _, _ in top_articles(days, limit * 2)]
        cache.set(key, ids, 300)
    return ids
