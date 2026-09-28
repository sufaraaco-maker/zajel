"""صندوق المعلومات في غرفة التحرير: لرئيس التحرير ومدير النظام فقط، وكل اطلاع مسجَّل."""

from __future__ import annotations

import time

from cryptography.fernet import InvalidToken
from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from arcms.accounts.models import User
from arcms.accounts.roles import ROLE_CAPABILITIES, Cap
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.tips import services
from arcms.tips.models import Tip, TipAttachment

from .base import paginate, requires

ATTACHMENT_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"
SEEN_KEY = "arcms_tips_seen"


def _handlers():
    roles = [r for r, caps in ROLE_CAPABILITIES.items() if Cap.TIPS in caps]
    return User.objects.filter(Q(role__in=roles) | Q(is_superuser=True), is_active=True)


@requires(Cap.TIPS)
def tips_list(request):
    status = request.GET.get("status", "open")
    qs = Tip.objects.select_related("assigned_to").annotate(
        n_files=Count("attachments", distinct=True), n_msgs=Count("messages", distinct=True)
    ).order_by("-unread", "-updated_at", "-pk")
    open_states = [Tip.Status.NEW, Tip.Status.REVIEWING]
    if status == "open":
        qs = qs.filter(status__in=open_states)
    elif status in Tip.Status.values:
        qs = qs.filter(status=status)
    counts = {
        "open": Tip.objects.filter(status__in=open_states).count(),
        "unread": Tip.objects.filter(unread=True).count(),
    }
    return render(
        request,
        "studio/tips_list.html",
        {"page": paginate(request, qs, 30), "status": status, "statuses": Tip.Status.choices, "counts": counts,
         "retention": services.retention_days()},
    )


def _log_access(request, tip: Tip) -> None:
    """يُسجَّل الاطلاع على البلاغ مرة في الساعة لكل جلسة، لا مع كل تحديث للصفحة."""
    seen = request.session.get(SEEN_KEY, {})
    now = time.time()
    if now - seen.get(str(tip.pk), 0) > 3600:
        record(Action.SECURITY, tip, message=f"اطلاع على البلاغ {tip.ref}")
        seen[str(tip.pk)] = now
        request.session[SEEN_KEY] = seen


@requires(Cap.TIPS)
def tip_detail(request, pk: int):
    tip = get_object_or_404(Tip.objects.select_related("assigned_to"), pk=pk)
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "reply":
            body = request.POST.get("body", "").strip()
            if body:
                services.add_newsroom_reply(tip, request.user, body)
                record(Action.UPDATE, tip, message=f"ردّ على المصدر في البلاغ {tip.ref}")
                messages.success(request, "أُرسل الرد. يقرؤه المصدر حين يفتح صفحة المتابعة برمزه.")
        elif action == "update":
            new_status = request.POST.get("status", tip.status)
            if new_status in Tip.Status.values:
                tip.status = new_status
            assignee = request.POST.get("assigned_to", "")
            tip.assigned_to = _handlers().filter(pk=int(assignee)).first() if assignee.isdigit() else None
            tip.note = request.POST.get("note", "").strip()[:5000]
            tip.save()
            record(Action.UPDATE, tip, message=f"تحديث البلاغ {tip.ref}: {tip.get_status_display()}")
            messages.success(request, "حُفظ.")
        return redirect("studio:tip_detail", pk=pk)
    if tip.unread:
        Tip.objects.filter(pk=pk).update(unread=False)
    _log_access(request, tip)
    return render(
        request,
        "studio/tip_detail.html",
        {
            "tip": tip,
            "thread": tip.messages.select_related("author").prefetch_related("attachments"),
            "attachments": tip.attachments.filter(message__isnull=True),
            "statuses": Tip.Status.choices,
            "handlers": _handlers(),
            "retention": services.retention_days(),
        },
    )


@requires(Cap.TIPS)
@require_POST
def tip_delete(request, pk: int):
    tip = get_object_or_404(Tip, pk=pk)
    ref = tip.ref
    tip.delete()
    record(Action.DELETE, message=f"حذف البلاغ {ref} نهائياً مع مرفقاته", object_type="tips.tip", object_id=str(pk))
    messages.success(request, f"حُذف البلاغ {ref} نهائياً.")
    return redirect("studio:tips")


@requires(Cap.TIPS)
def tip_attachment(request, pk: int, att: int):
    attachment = get_object_or_404(TipAttachment.objects.select_related("tip"), pk=att, tip_id=pk)
    try:
        data = services.read_attachment(attachment)
    except InvalidToken:
        return HttpResponse("تعذّر فك تشفير المرفق: مفتاح التشفير لا يطابق.", status=500, content_type="text/plain; charset=utf-8")
    inline = request.GET.get("inline") == "1" and attachment.kind == TipAttachment.Kind.IMAGE
    response = HttpResponse(data, content_type=attachment.mime)
    name = f"{attachment.tip.ref}-{attachment.pk}.{attachment.ext}"
    response["Content-Disposition"] = f'{"inline" if inline else "attachment"}; filename="{name}"'
    response["Content-Security-Policy"] = ATTACHMENT_CSP
    response["Cache-Control"] = "no-store"
    if not inline:
        record(Action.SECURITY, attachment.tip, message=f"تنزيل مرفق من البلاغ {attachment.tip.ref}")
    return response
