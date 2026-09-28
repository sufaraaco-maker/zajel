"""تجميع المشاهدات يومياً، وحذف الأملاح والسجلات الخام القديمة."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from arcms.core.jobs import periodic

from .models import DailyArticleStat, DailySalt, DailySiteStat, PageView


def _day_bounds(day: date):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def aggregate_day(day: date) -> DailySiteStat:
    start, end = _day_bounds(day)
    views = PageView.objects.filter(ts__gte=start, ts__lt=end)
    with transaction.atomic():
        DailyArticleStat.objects.filter(day=day).delete()
        rows = (
            views.exclude(article_id__isnull=True)
            .values("article_id")
            .annotate(v=Count("id"), u=Count("visitor", distinct=True))
        )
        from arcms.content.models import Article

        existing = set(Article.objects.filter(pk__in=[r["article_id"] for r in rows]).values_list("pk", flat=True))
        DailyArticleStat.objects.bulk_create(
            [
                DailyArticleStat(day=day, article_id=r["article_id"], views=r["v"], visitors=r["u"])
                for r in rows
                if r["article_id"] in existing
            ]
        )

        def group(field, limit=None):
            qs = views.values(field).annotate(n=Count("id")).order_by("-n")
            if limit:
                qs = qs[:limit]
            return {str(r[field] or ""): r["n"] for r in qs}

        stat, _ = DailySiteStat.objects.update_or_create(
            day=day,
            defaults={
                "views": views.count(),
                "visitors": views.values("visitor").distinct().count(),
                "by_source": group("source"),
                "by_device": group("device"),
                "by_referrer": {k: v for k, v in group("referrer_host", 25).items() if k},
                "by_category": {k: v for k, v in group("category_id", 50).items() if k},
            },
        )
    return stat


@periodic("analytics_aggregate", 600)
def aggregate_recent():
    today = timezone.localdate()
    aggregate_day(today)
    # نعيد تجميع الأمس مرة بعد منتصف الليل لالتقاط آخر الزيارات.
    if not DailySiteStat.objects.filter(day=today - timedelta(days=1)).exists() or timezone.localtime().hour == 0:
        aggregate_day(today - timedelta(days=1))


@periodic("analytics_purge", 3600)
def purge():
    today = timezone.localdate()
    # ملح الأمس وما قبله يُحذف: البصمات القديمة تصبح غير قابلة للربط بأي عنوان.
    DailySalt.objects.filter(day__lt=today).delete()
    # بقايا الإصدارات السابقة التي كانت تحفظ الملح في الذاكرة المؤقتة (قد تكون جدولاً في القاعدة)
    from django.core.cache import cache

    cache.delete_many([f"arcms:salt:{today - timedelta(days=n):%Y-%m-%d}" for n in range(0, 40)])
    cutoff = timezone.now() - timedelta(days=settings.ARCMS_ANALYTICS_RAW_DAYS)
    PageView.objects.filter(ts__lt=cutoff).delete()
