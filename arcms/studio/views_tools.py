"""أدوات غرفة التحرير: دمج الوسوم، مشتركو النشرة، والتقويم التحريري."""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta

from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.arabic.dates import WEEKDAYS
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.content.models import Article, Status, Tag
from arcms.content.tags import merge_tags, similar_groups
from arcms.distribution.models import NewsletterSubscriber

from .base import paginate, requires

# --- الوسوم ---


def tags_list_extra(request) -> dict:
    return {"similar": similar_groups(20)}


def tag_form_extra(request, obj) -> dict:
    if obj is None:
        return {}
    others = Tag.objects.exclude(pk=obj.pk).order_by("name")
    return {"merge_targets": others[:500], "article_count": obj.articles.count()}


@requires(Cap.TAXONOMY)
@require_POST
def tag_merge(request, pk: int):
    source = get_object_or_404(Tag, pk=pk)
    target_id = request.POST.get("target", "")
    target = Tag.objects.filter(pk=int(target_id)).first() if target_id.isdigit() else None
    if target is None:
        messages.error(request, "اختر الوسم الذي يُدمج فيه.")
        return redirect("studio:tags_edit", pk=pk)
    try:
        moved = merge_tags(source, target)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("studio:tags_edit", pk=pk)
    messages.success(request, f"دُمج الوسم في «{target.name}» ونُقلت {moved} مادة، وحُوّل رابطه القديم.")
    return redirect("studio:tags_list")


@requires(Cap.TAXONOMY)
@require_POST
def tag_merge_group(request):
    """دمج مجموعة اقتراح كاملة في الوسم الأكثر استخداماً (أول عنصر)."""
    ids = [int(i) for i in request.POST.getlist("tag") if i.isdigit()]
    target_id = request.POST.get("target", "")
    if not target_id.isdigit() or int(target_id) not in ids:
        messages.error(request, "اختر الوسم الذي تبقى عليه المواد.")
        return redirect("studio:tags_list")
    target = get_object_or_404(Tag, pk=int(target_id))
    total = 0
    for tag in Tag.objects.filter(pk__in=ids).exclude(pk=target.pk):
        total += merge_tags(tag, target)
    messages.success(request, f"دُمجت الوسوم في «{target.name}» ({total} مادة).")
    return redirect("studio:tags_list")


# --- مشتركو النشرة ---


@requires(Cap.DIST_MANAGE)
def subscribers(request):
    qs = NewsletterSubscriber.objects.all()
    q = request.GET.get("q", "").strip()
    state = request.GET.get("state", "")
    if q:
        qs = qs.filter(email__icontains=q)
    if state == "active":
        qs = qs.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True)
    elif state == "pending":
        qs = qs.filter(confirmed_at__isnull=True, unsubscribed_at__isnull=True)
    elif state == "left":
        qs = qs.filter(unsubscribed_at__isnull=False)
    counts = {
        "active": NewsletterSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True).count(),
        "pending": NewsletterSubscriber.objects.filter(confirmed_at__isnull=True, unsubscribed_at__isnull=True).count(),
        "left": NewsletterSubscriber.objects.filter(unsubscribed_at__isnull=False).count(),
    }
    return render(request, "studio/subscribers.html", {"page": paginate(request, qs, 50), "q": q, "state": state, "counts": counts})


def _csv_safe(value: str) -> str:
    """خلية تبدأ بـ = أو + أو - أو @ قد ينفّذها برنامج الجداول صيغةً."""
    value = value or ""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


@requires(Cap.DIST_MANAGE)
def subscribers_export(request):
    rows = NewsletterSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True).order_by("created_at")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="newsletter-subscribers.csv"'
    response.write("﻿")  # ليفتحه Excel بترميز صحيح
    writer = csv.writer(response)
    writer.writerow(["email", "confirmed_at", "source"])
    count = 0
    for sub in rows.iterator():
        writer.writerow([_csv_safe(sub.email), sub.confirmed_at.isoformat(), _csv_safe(sub.source)])
        count += 1
    record(Action.SECURITY, message=f"تصدير قائمة مشتركي النشرة ({count} بريد)", object_type="distribution.newslettersubscriber")
    return response


@requires(Cap.DIST_MANAGE)
@require_POST
def subscriber_delete(request, pk: int):
    sub = get_object_or_404(NewsletterSubscriber, pk=pk)
    sub.delete()
    record(Action.DELETE, message="حذف مشترك من النشرة بناءً على طلبه", object_type="distribution.newslettersubscriber",
           object_id=str(pk))
    messages.success(request, "حُذف البريد نهائياً من قائمة النشرة.")
    return redirect("studio:subscribers")


# --- التقويم التحريري ---

WEEK_START = 5  # السبت (Monday=0) كما في أغلب غرف الأخبار العربية


def _week_start(day: date) -> date:
    return day - timedelta(days=(day.weekday() - WEEK_START) % 7)


@requires(Cap.ARTICLE_PUBLISH, Cap.ARTICLE_REVIEW)
def calendar(request):
    try:
        anchor = date.fromisoformat(request.GET.get("week", ""))
    except ValueError:
        anchor = timezone.localdate()
    start = _week_start(anchor)
    days = [start + timedelta(days=i) for i in range(7)]
    tz = timezone.get_current_timezone()
    begin = datetime.combine(start, time.min, tzinfo=tz)
    end = begin + timedelta(days=7)
    qs = (
        Article.objects.filter(
            Q(status=Status.SCHEDULED, scheduled_at__gte=begin, scheduled_at__lt=end)
            | Q(status=Status.PUBLISHED, published_at__gte=begin, published_at__lt=end)
        )
        .select_related("category", "created_by")
        .order_by("scheduled_at", "published_at")
    )
    desks = request.user.desk_ids()
    if desks:
        qs = qs.filter(category_id__in=desks)
    by_day: dict[date, list] = {d: [] for d in days}
    for a in qs:
        when = a.scheduled_at if a.status == Status.SCHEDULED else a.published_at
        local = timezone.localtime(when)
        if local.date() in by_day:
            by_day[local.date()].append({"article": a, "time": local, "scheduled": a.status == Status.SCHEDULED})
    columns = [
        {"day": d, "weekday": WEEKDAYS[d.weekday()], "items": sorted(by_day[d], key=lambda x: x["time"]), "today": d == timezone.localdate()}
        for d in days
    ]
    return render(
        request,
        "studio/calendar.html",
        {
            "columns": columns,
            "prev": (start - timedelta(days=7)).isoformat(),
            "next": (start + timedelta(days=7)).isoformat(),
            "this_week": _week_start(timezone.localdate()) == start,
            "scheduled_total": sum(1 for c in columns for i in c["items"] if i["scheduled"]),
            "published_total": sum(1 for c in columns for i in c["items"] if not i["scheduled"]),
        },
    )
