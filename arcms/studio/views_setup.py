"""معالج إعداد نسخة جديدة لمؤسسة: الهوية والشعار والمظهر والبنية والمنصات في صفحة واحدة."""

from __future__ import annotations

from io import StringIO

from django import forms
from django.contrib import messages
from django.core.management import call_command
from django.shortcuts import redirect, render

from arcms.accounts.roles import Cap
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.core.models import HomeBlock, SiteSettings
from arcms.core.presets import PRESETS, THEMES, apply_theme

from .base import requires

SOCIAL_FIELDS = ["telegram", "whatsapp", "x_twitter", "facebook", "instagram", "youtube", "tiktok"]


class SetupForm(forms.ModelForm):
    theme = forms.ChoiceField(
        label="المظهر", choices=[("", "أبقِ المظهر الحالي")] + [(k, t["label"]) for k, t in THEMES.items()], required=False
    )
    structure = forms.ChoiceField(
        label="بنية الموقع",
        choices=[("", "أبقِ الأقسام والقوائم والصفحة الرئيسية كما هي")] + [(k, p["label"]) for k, p in PRESETS.items()],
        required=False,
        help_text="تنشئ الأقسام الناقصة وتعيد بناء القوائم وكتل الصفحة الرئيسية. لا تحذف أي مادة ولا قسماً موجوداً.",
    )

    class Meta:
        model = SiteSettings
        fields = ["name", "short_name", "tagline", "description", "logo", "logo_dark", "primary_color", "accent_color",
                  *SOCIAL_FIELDS, "alt_language_url", "contact_email", "header_cta_label", "header_cta_url"]
        widgets = {"logo": forms.HiddenInput(), "logo_dark": forms.HiddenInput(),
                   "primary_color": forms.TextInput(attrs={"type": "color"}),
                   "accent_color": forms.TextInput(attrs={"type": "color"}),
                   "description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("primary_color", "accent_color"):
            self.fields[name].required = False

    def clean_primary_color(self):
        return (self.cleaned_data.get("primary_color") or self.instance.primary_color).lower()

    def clean_accent_color(self):
        return (self.cleaned_data.get("accent_color") or self.instance.accent_color).lower()

    def clean_header_cta_url(self):
        from .forms import _clean_link

        return _clean_link(self.cleaned_data.get("header_cta_url"))


@requires(Cap.SETTINGS)
def setup(request):
    site = SiteSettings.objects.get(pk=SiteSettings.load().pk)
    form = SetupForm(request.POST or None, instance=site)
    if request.method == "POST" and form.is_valid():
        site = form.save()
        done = ["الهوية والمنصات"]
        if form.cleaned_data["structure"]:
            call_command("arcms_setup", preset=form.cleaned_data["structure"], force=True, stdout=StringIO())
            done.append(f"بنية «{PRESETS[form.cleaned_data['structure']]['label']}»")
        if form.cleaned_data["theme"]:
            themed = SiteSettings.objects.get(pk=site.pk)
            apply_theme(themed, form.cleaned_data["theme"])
            done.append(f"مظهر «{THEMES[form.cleaned_data['theme']]['label']}»")
            # ألوان الهوية التي اختارها المستخدم بنفسه تتقدّم على ألوان المظهر
            custom = {
                f: form.cleaned_data[f]
                for f in ("primary_color", "accent_color")
                if (form.data.get(f) or "").lower() not in ("", (form.initial.get(f) or "").lower())
            }
            if custom:
                for field, value in custom.items():
                    setattr(themed, field, value)
                themed.save()
                done.append("ألوان الهوية")
        # رابط بطاقة الترويج للقناة يتبع رابط تيليجرام ما لم يُضبط غيره
        if site.telegram:
            HomeBlock.objects.filter(kind=HomeBlock.Kind.PROMO, link="").update(link=site.telegram)
        record(Action.UPDATE, site, message="معالج الإعداد: " + "، ".join(done))
        messages.success(request, "طُبّق: " + "، ".join(done) + ". افتح الموقع لترى النتيجة.")
        return redirect("studio:setup")
    steps = [
        ("الشعار", bool(site.logo_id)),
        ("المنصات", any(getattr(site, f) for f in SOCIAL_FIELDS)),
        ("بريد التواصل", bool(site.contact_email)),
        ("كتل الصفحة الرئيسية", HomeBlock.objects.filter(is_active=True).exists()),
    ]
    from .views_admin import logo_palette

    return render(request, "studio/setup.html", {"form": form, "themes": THEMES, "presets": PRESETS, "steps": steps,
                                                 "logo_palette": logo_palette(site)})
