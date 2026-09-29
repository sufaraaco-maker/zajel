"""مكتبة الوسائط: رفع (مع نزع البيانات المخفية)، تصفح، تعديل البيانات الوصفية."""

from __future__ import annotations

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.content.imaging import ImageRejected, store_image
from arcms.content.models import MediaAsset

from .base import paginate, requires
from .forms import MediaMetaForm


def _asset_json(a: MediaAsset) -> dict:
    return {
        "id": a.pk,
        "thumb": a.thumb,
        "card": a.card,
        "large": a.large,
        "url": a.url,
        "caption": a.caption,
        "credit": a.credit,
        "alt": a.alt_text,
        "width": a.width,
        "height": a.height,
        "removed": a.removed_metadata,
        "sensitive": a.sensitive,
    }


@requires(Cap.MEDIA_UPLOAD, Cap.MEDIA_MANAGE)
def library(request):
    qs = MediaAsset.objects.select_related("uploaded_by")
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(caption__icontains=q) | Q(credit__icontains=q))
    if request.GET.get("mine"):
        qs = qs.filter(uploaded_by=request.user)
    page = paginate(request, qs, 48)
    return render(request, "studio/media.html", {"page": page, "q": q})


@requires(Cap.MEDIA_UPLOAD, Cap.MEDIA_MANAGE)
@require_POST
def upload(request):
    """يقبل ملفاً واحداً أو أكثر. يرد JSON إن طُلب، وإلا يعيد التوجيه للمكتبة."""
    files = request.FILES.getlist("file") or request.FILES.getlist("files")
    wants_json = "application/json" in request.headers.get("Accept", "") or request.POST.get("json")
    results, errors = [], []
    for f in files[:30]:
        try:
            asset, created = store_image(
                f.read(),
                user=request.user,
                caption=request.POST.get("caption", ""),
                credit=request.POST.get("credit", ""),
                title=request.POST.get("title", ""),
            )
            results.append({**_asset_json(asset), "created": created})
        except ImageRejected as exc:
            errors.append(f"{f.name}: {exc}")
    if wants_json:
        return JsonResponse({"items": results, "errors": errors}, status=200 if results or not errors else 400)
    for e in errors:
        messages.error(request, e)
    if results:
        removed = sorted({r for item in results for r in item["removed"]})
        msg = f"رُفعت {len(results)} صورة."
        if removed:
            msg += " نُزعت منها: " + "، ".join(removed) + "."
        messages.success(request, msg)
    return redirect("studio:media")


@requires(Cap.MEDIA_UPLOAD, Cap.MEDIA_MANAGE)
def picker(request):
    qs = MediaAsset.objects.all()
    if request.GET.get("id", "").isdigit():
        asset = qs.filter(pk=int(request.GET["id"])).first()
        return JsonResponse({"items": [_asset_json(asset)] if asset else [], "next": None})
    q = request.GET.get("q", "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(caption__icontains=q) | Q(credit__icontains=q))
    try:
        offset = max(0, int(request.GET.get("offset", 0)))
    except ValueError:
        offset = 0
    items = list(qs[offset : offset + 40])
    return JsonResponse({"items": [_asset_json(a) for a in items], "next": offset + 40 if len(items) == 40 else None})


@requires(Cap.MEDIA_UPLOAD, Cap.MEDIA_MANAGE)
def edit(request, pk: int):
    asset = get_object_or_404(MediaAsset, pk=pk)
    if asset.uploaded_by_id != request.user.pk and not request.user.can(Cap.MEDIA_MANAGE):
        if request.method == "POST":
            raise PermissionDenied
    form = MediaMetaForm(request.POST or None, instance=asset)
    if request.method == "POST" and form.is_valid():
        saved = form.save()
        if {"focal_x", "focal_y"} & set(form.changed_data):
            from arcms.content.imaging import recrop

            recrop(saved)
        if "sensitive" in form.changed_data:
            from arcms.content.signals import invalidate_public_cache

            invalidate_public_cache()  # الرئيسية والأقسام مخزنة مؤقتاً بالصورة كما كانت
        messages.success(request, "حُفظت بيانات الصورة.")
        return redirect("studio:media")
    return render(request, "studio/media_edit.html", {"asset": asset, "form": form})


@requires(Cap.MEDIA_MANAGE)
@require_POST
def delete(request, pk: int):
    asset = get_object_or_404(MediaAsset, pk=pk)
    from django.core.files.storage import default_storage

    paths = [asset.file.name, *(asset.renditions or {}).values()]
    asset.delete()
    for p in paths:
        if p:
            default_storage.delete(p)
    messages.success(request, "حُذفت الصورة من الخادم.")
    return redirect("studio:media")
