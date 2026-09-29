"""مكتب الوكالات في غرفة التحرير: قراءة ما تبثه الوكالات، والتنبيهات، والاعتماد مسودةً."""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.arabic.normalize import normalize
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.wires import services
from arcms.wires.models import WireItem, WireKeyword, WireSource

from .base import paginate, requires

TABS = (
    ("new", "جديد"),
    ("alerts", "تنبيهات"),
    ("all", "الكل"),
    ("adopted", "اعتُمد"),
    ("ignored", "تُجوهل"),
)


@requires(Cap.WIRES)
def desk(request):
    tab = request.GET.get("tab", "new")
    if tab not in dict(TABS):
        tab = "new"
    qs = WireItem.objects.select_related("source", "article", "handled_by")
    if tab == "new":
        qs = qs.filter(status=WireItem.Status.NEW)
    elif tab == "alerts":
        qs = qs.filter(is_alert=True).exclude(status=WireItem.Status.IGNORED)
    elif tab == "adopted":
        qs = qs.filter(status=WireItem.Status.ADOPTED)
    elif tab == "ignored":
        qs = qs.filter(status=WireItem.Status.IGNORED)
    source = request.GET.get("source", "")
    if source.isdigit():
        qs = qs.filter(source_id=int(source))
    q = request.GET.get("q", "").strip()
    if q:
        for word in normalize(q).split()[:6]:
            qs = qs.filter(search_text__contains=word)
    since = timezone.now() - timedelta(hours=12)
    counts = {
        "new": WireItem.objects.filter(status=WireItem.Status.NEW).count(),
        "alerts": WireItem.objects.filter(is_alert=True, status=WireItem.Status.NEW, fetched_at__gte=since).count(),
    }
    return render(request, "studio/wires.html", {
        "page": paginate(request, qs, 40), "tab": tab, "tabs": TABS, "counts": counts, "q": q, "source": source,
        "sources": WireSource.objects.all(), "failing": WireSource.objects.filter(is_active=True).exclude(last_error=""),
        "can_adopt": request.user.can(Cap.ARTICLE_CREATE),
    })


@requires(Cap.WIRES)
@require_POST
def action(request):
    back = request.POST.get("next") or reverse("studio:wires")
    if not back.startswith("/studio/"):
        back = reverse("studio:wires")
    what = request.POST.get("action")
    if what == "adopt":
        if not request.user.can(Cap.ARTICLE_CREATE):
            raise PermissionDenied("لا تملك صلاحية إنشاء المواد.")
        item = get_object_or_404(WireItem, pk=request.POST.get("item"))
        article = services.adopt(item, request.user)
        messages.success(request, "أُنشئت مسودة من مادة الوكالة مع نسبتها. حرّرها قبل إرسالها للمراجعة.")
        return redirect("studio:article_edit", article.pk)
    if what == "ignore":
        ids = [int(i) for i in request.POST.getlist("ids") if i.isdigit()][:200]
        n = services.ignore(ids, request.user)
        messages.success(request, f"تُجوهلت {n} مادة." if n else "لم تُحدَّد مواد جديدة.")
    elif what == "restore":
        WireItem.objects.filter(pk=request.POST.get("item"), status=WireItem.Status.IGNORED).update(
            status=WireItem.Status.NEW, handled_by=None, handled_at=None
        )
    return redirect(back)


def sources_extra(request):
    return {"keywords": "\n".join(WireKeyword.objects.values_list("word", flat=True))}


@requires(Cap.WIRES_MANAGE)
@require_POST
def keywords(request):
    words = services.set_watchwords(request.POST.get("keywords", ""))
    record(Action.SETTINGS, message=f"كلمات التنبيه في مكتب الوكالات: {len(words)} كلمة", object_type="wires.wirekeyword")
    messages.success(request, f"حُفظت {len(words)} كلمة تنبيه، وأُعيد فحص مواد اليوم.")
    return redirect("studio:wiresources_list")


@requires(Cap.WIRES_MANAGE)
@require_POST
def poll_now(request, pk: int):
    source = get_object_or_404(WireSource, pk=pk)
    n = services.poll(source)
    if source.last_error:
        messages.error(request, f"تعذّر جلب «{source.name}»: {source.last_error}")
    else:
        messages.success(request, f"جُلب «{source.name}»: {n} مادة جديدة.")
    return redirect("studio:wiresources_list")
