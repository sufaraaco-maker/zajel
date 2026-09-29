"""مدقق الأسلوب: دليل المؤسسة (مصطلحاتها المفضلة والمتجنبة) وواجهة الفحص التي يستدعيها المحرر."""

from __future__ import annotations

import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.arabic import style
from arcms.core.models import SiteSettings

from .base import requires
from .forms import StyleGuideForm

MAX_FIELD = 150_000
MAX_FIELDS = 12


@requires(Cap.STYLE)
def style_guide(request):
    site = SiteSettings.load()
    form = StyleGuideForm(request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        form.save()
        rules = len(style.parse_rules(form.cleaned_data["style_rules"])[0])
        if form.rule_errors:
            messages.warning(request, "حُفظ الدليل، لكن بعض الأسطر أُهملت: " + " · ".join(form.rule_errors[:5]))
        else:
            messages.success(request, f"حُفظ الدليل ({rules} قاعدة).")
        return redirect("studio:style")
    rules, errors = style.parse_rules(site.style_rules)
    return render(request, "studio/style_guide.html", {
        "form": form, "rule_count": len(rules), "rule_errors": errors, "checks": style.CHECKS,
    })


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY, Cap.STYLE)
@require_POST
def api_style(request):
    """يستقبل {"fields": {"title": "...", "body": "..."}} ويعيد الملاحظات لكل حقل بمواضع المتصفح."""
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return JsonResponse({"error": "طلب غير صالح."}, status=400)
    fields = data.get("fields") if isinstance(data, dict) else None
    if not isinstance(fields, dict) or len(fields) > MAX_FIELDS:
        return JsonResponse({"error": "طلب غير صالح."}, status=400)
    site = SiteSettings.load()
    disabled = frozenset(site.style_disabled or [])
    out, total = {}, 0
    for name, text in fields.items():
        if not isinstance(name, str) or not isinstance(text, str) or len(name) > 40:
            return JsonResponse({"error": "طلب غير صالح."}, status=400)
        if len(text) > MAX_FIELD:
            return JsonResponse({"error": "النص أطول من أن يُدقَّق دفعة واحدة."}, status=413)
        out[name] = style.report(text, rules=site.style_rules, disabled=disabled)
        total += len(out[name])
    return JsonResponse({"fields": out, "total": total})
