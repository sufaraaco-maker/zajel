"""العاجل والتغطيات المباشرة."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.content.models import BreakingNews, LiveCoverage, LiveEntry

from .base import paginate, requires
from .forms import BreakingForm, LiveCoverageForm, LiveEntryForm


@requires(Cap.BREAKING)
def breaking(request):
    form = BreakingForm(request.POST or None, initial={"send_telegram": True, "send_push": True})
    if request.method == "POST" and form.is_valid():
        item = form.save(commit=False)
        item.created_by = request.user
        item.save()
        from django.db import transaction

        from arcms.distribution.dispatch import on_breaking_created

        transaction.on_commit(lambda: on_breaking_created(item.pk))
        messages.success(request, "نُشر العاجل في الشريط وأُرسل إلى القنوات المختارة.")
        return redirect("studio:breaking")
    page = paginate(request, BreakingNews.objects.select_related("created_by", "article"), 30)
    return render(request, "studio/breaking.html", {"form": form, "page": page, "now": timezone.now()})


@requires(Cap.BREAKING)
@require_POST
def breaking_toggle(request, pk: int):
    item = get_object_or_404(BreakingNews, pk=pk)
    item.is_active = not item.is_active
    item.save()
    return redirect("studio:breaking")


@requires(Cap.LIVE_POST, Cap.LIVE_MANAGE)
def live_list(request):
    form = LiveCoverageForm(request.POST or None) if request.user.can(Cap.LIVE_MANAGE) else None
    if form is not None and request.method == "POST" and form.is_valid():
        live = form.save(commit=False)
        live.created_by = request.user
        live.save()
        messages.success(request, "بدأت التغطية المباشرة.")
        return redirect("studio:live_detail", pk=live.pk)
    page = paginate(request, LiveCoverage.objects.all(), 20)
    return render(request, "studio/live_list.html", {"form": form, "page": page})


@requires(Cap.LIVE_POST, Cap.LIVE_MANAGE)
def live_detail(request, pk: int):
    live = get_object_or_404(LiveCoverage, pk=pk)
    form = LiveEntryForm(request.POST or None)
    if request.method == "POST" and "body" in request.POST and form.is_valid():
        entry = form.save(commit=False)
        entry.coverage = live
        entry.author = request.user
        if entry.is_pinned and not request.user.can(Cap.LIVE_MANAGE):
            entry.is_pinned = False
        entry.save()
        messages.success(request, "أُضيف التحديث.")
        return redirect("studio:live_detail", pk=pk)
    settings_form = LiveCoverageForm(instance=live) if request.user.can(Cap.LIVE_MANAGE) else None
    entries = live.entries.select_related("author", "image")[:100]
    return render(request, "studio/live_detail.html", {"live": live, "form": form, "entries": entries, "settings_form": settings_form})


@requires(Cap.LIVE_MANAGE)
@require_POST
def live_update(request, pk: int):
    live = get_object_or_404(LiveCoverage, pk=pk)
    form = LiveCoverageForm(request.POST, instance=live)
    if form.is_valid():
        live = form.save(commit=False)
        if not live.is_live and not live.ended_at:
            live.ended_at = timezone.now()
        if live.is_live:
            live.ended_at = None
        live.save()
        messages.success(request, "حُفظت إعدادات التغطية.")
    return redirect("studio:live_detail", pk=pk)


@requires(Cap.LIVE_MANAGE)
@require_POST
def live_entry_action(request, pk: int, entry: int):
    item = get_object_or_404(LiveEntry, pk=entry, coverage_id=pk)
    action = request.POST.get("action")
    if action == "delete":
        item.delete()
    elif action == "pin":
        item.is_pinned = not item.is_pinned
        item.save()
    return redirect("studio:live_detail", pk=pk)
