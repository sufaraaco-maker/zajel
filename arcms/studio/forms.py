from __future__ import annotations

from django import forms
from django.utils import timezone

from arcms.content.models import (
    Article,
    ArticleKind,
    Author,
    BreakingNews,
    Category,
    Dossier,
    LiveCoverage,
    LiveEntry,
    MediaAsset,
    Page,
    Tag,
)
from arcms.content.sanitize import sanitize_html
from arcms.core.models import AdSlot, HomeBlock, MenuItem, SiteSettings
from arcms.distribution.models import ChannelConfig, WhatsAppSubscriber


class DateTimeLocal(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)

    def format_value(self, value):
        if value and hasattr(value, "tzinfo") and value.tzinfo:
            value = timezone.localtime(value)
        return super().format_value(value)


class ArticleForm(forms.ModelForm):
    tag_names = forms.CharField(
        label="الوسوم",
        required=False,
        help_text="افصل بين الوسوم بفاصلة (،). الوسم الموجود يُعاد استخدامه ولو اختلفت الهمزات.",
        widget=forms.TextInput(attrs={"data-autocomplete": "tags", "autocomplete": "off"}),
    )
    related_ids = forms.CharField(required=False, widget=forms.HiddenInput)
    gallery_ids = forms.CharField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Article
        fields = [
            "kind", "kicker", "title", "subtitle", "excerpt", "dateline", "body",
            "category", "extra_categories", "authors", "dossiers",
            "featured_image", "image_caption", "hide_featured_image", "video_url",
            "source", "source_url", "source_notes", "correction",
            "is_breaking", "is_featured", "is_exclusive", "priority", "featured_until",
            "allow_indexing", "seo_title", "seo_description",
            "send_telegram", "send_whatsapp", "send_push", "in_newsletter",
        ]
        widgets = {
            "kind": forms.RadioSelect,
            "title": forms.TextInput(attrs={"class": "input-title", "placeholder": "العنوان", "maxlength": 250}),
            "subtitle": forms.TextInput(attrs={"placeholder": "عنوان فرعي (اختياري)"}),
            "excerpt": forms.Textarea(attrs={"rows": 3}),
            "body": forms.HiddenInput(),
            "source_notes": forms.Textarea(attrs={"rows": 3, "class": "sensitive"}),
            "correction": forms.Textarea(attrs={"rows": 2}),
            "extra_categories": forms.SelectMultiple(attrs={"size": 5}),
            "authors": forms.SelectMultiple(attrs={"size": 6}),
            "dossiers": forms.SelectMultiple(attrs={"size": 4}),
            "featured_image": forms.HiddenInput(),
            "featured_until": DateTimeLocal(),
            "seo_description": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        cats = Category.objects.filter(is_active=True)
        if user is not None:
            desks = user.desk_ids()
            if desks:
                cats = cats.filter(pk__in=desks)
        self.fields["category"].queryset = cats
        self.fields["category"].required = False
        self.fields["extra_categories"].queryset = Category.objects.filter(is_active=True)
        self.fields["featured_image"].queryset = MediaAsset.objects.all()
        if self.instance.pk:
            self.fields["tag_names"].initial = "، ".join(self.instance.tags.values_list("name", flat=True))
            self.fields["related_ids"].initial = ",".join(str(i) for i in self.instance.related.values_list("pk", flat=True))
            self.fields["gallery_ids"].initial = ",".join(
                str(i) for i in self.instance.gallery_items.values_list("media_id", flat=True)
            )
        from arcms.content.workflow import can_view_sources

        if user is not None and self.instance.pk and not can_view_sources(user, self.instance):
            del self.fields["source_notes"]
        # كل الحقول تنتمي إلى نموذج المحرر ولو وُضعت في العمود الجانبي خارج وسم <form>.
        for field in self.fields.values():
            field.widget.attrs["form"] = "article-form"

    def clean_body(self):
        return sanitize_html(self.cleaned_data.get("body", ""))

    def clean_title(self):
        from arcms.arabic.text import clean_headline

        return clean_headline(self.cleaned_data["title"])

    def save_tags(self, article: Article) -> None:
        raw = self.cleaned_data.get("tag_names", "")
        names = [n.strip() for n in raw.replace("،", ",").split(",") if n.strip()]
        article.tags.set([Tag.get_or_create_by_name(n) for n in names[:25]])
        ids = [int(i) for i in (self.cleaned_data.get("related_ids") or "").split(",") if i.strip().isdigit()]
        article.related.set(Article.objects.filter(pk__in=ids).exclude(pk=article.pk)[:8])
        from arcms.content.models import GalleryItem

        gallery = [int(i) for i in (self.cleaned_data.get("gallery_ids") or "").split(",") if i.strip().isdigit()]
        existing = list(article.gallery_items.values_list("media_id", flat=True))
        if gallery != existing:
            article.gallery_items.all().delete()
            valid = set(MediaAsset.objects.filter(pk__in=gallery).values_list("pk", flat=True))
            GalleryItem.objects.bulk_create(
                [GalleryItem(article=article, media_id=m, order=n) for n, m in enumerate(gallery) if m in valid]
            )


class BreakingForm(forms.ModelForm):
    class Meta:
        model = BreakingNews
        fields = ["text", "article", "link", "expires_at", "send_telegram", "send_push", "send_whatsapp"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 2, "maxlength": 280, "autofocus": True}),
            "article": forms.HiddenInput(),
            "expires_at": DateTimeLocal(),
        }


class LiveCoverageForm(forms.ModelForm):
    class Meta:
        model = LiveCoverage
        fields = ["title", "summary", "category", "cover", "is_live"]
        widgets = {"summary": forms.Textarea(attrs={"rows": 3}), "cover": forms.HiddenInput()}


class LiveEntryForm(forms.ModelForm):
    class Meta:
        model = LiveEntry
        fields = ["body", "image", "is_important", "is_pinned"]
        widgets = {"body": forms.Textarea(attrs={"rows": 3, "autofocus": True}), "image": forms.HiddenInput()}

    def clean_body(self):
        return sanitize_html(self.cleaned_data["body"].replace("\n", "<br>"))


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "slug", "parent", "description", "color", "order", "is_active", "desk_members"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "desk_members": forms.SelectMultiple(attrs={"size": 8}),
        }


class TagForm(forms.ModelForm):
    class Meta:
        model = Tag
        fields = ["name", "slug"]


class AuthorForm(forms.ModelForm):
    class Meta:
        model = Author
        fields = ["name", "slug", "title", "bio", "photo", "x_handle", "user", "is_columnist", "show_page"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 3}), "photo": forms.HiddenInput()}


class DossierForm(forms.ModelForm):
    class Meta:
        model = Dossier
        fields = ["title", "slug", "description", "cover", "color", "order", "is_active"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "cover": forms.HiddenInput(),
            "color": forms.TextInput(attrs={"type": "color"}),
        }


class PageForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ["title", "slug", "body", "is_published", "show_contact_form", "order"]
        widgets = {"body": forms.HiddenInput()}

    def clean_body(self):
        return sanitize_html(self.cleaned_data.get("body", ""))


class MenuItemForm(forms.ModelForm):
    class Meta:
        model = MenuItem
        fields = ["location", "label", "link_type", "category", "kind", "dossier", "page", "tag", "url", "parent",
                  "order", "highlight", "new_tab", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kind"] = forms.ChoiceField(label="نوع المادة", choices=[("", "—")] + list(ArticleKind.choices), required=False)
        self.fields["parent"].queryset = MenuItem.objects.filter(parent__isnull=True)


class HomeBlockForm(forms.ModelForm):
    class Meta:
        model = HomeBlock
        fields = ["kind", "title", "layout", "count", "category", "categories", "dossier", "article_kind", "ad_slot",
                  "html", "dark", "order", "is_active"]
        widgets = {"html": forms.Textarea(attrs={"rows": 4, "dir": "ltr"}), "categories": forms.SelectMultiple(attrs={"size": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["article_kind"] = forms.ChoiceField(
            label="نوع المادة", choices=[("", "—")] + list(ArticleKind.choices), required=False
        )


class AdSlotForm(forms.ModelForm):
    class Meta:
        model = AdSlot
        fields = ["name", "placement", "image", "link", "html", "starts_at", "ends_at", "is_active"]
        widgets = {
            "image": forms.HiddenInput(),
            "html": forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
            "starts_at": DateTimeLocal(),
            "ends_at": DateTimeLocal(),
        }


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        exclude = ["updated_at"]
        widgets = {
            "logo": forms.HiddenInput(),
            "logo_dark": forms.HiddenInput(),
            "default_share_image": forms.HiddenInput(),
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "accent_color": forms.TextInput(attrs={"type": "color"}),
            "description": forms.Textarea(attrs={"rows": 2}),
            "footer_about": forms.Textarea(attrs={"rows": 3}),
            "custom_head_html": forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
        }

    SECTIONS = (
        ("الهوية", ["name", "short_name", "tagline", "description", "logo", "logo_dark", "default_share_image"]),
        ("الألوان والخطوط", ["primary_color", "accent_color", "header_dark", "font_headings", "font_body"]),
        ("التاريخ والأرقام", ["month_style", "digits", "clock", "show_hijri", "hijri_adjust"]),
        ("شريط العاجل", ["ticker_enabled", "ticker_label", "ticker_hours"]),
        ("روابط التواصل", ["telegram", "whatsapp", "x_twitter", "facebook", "instagram", "youtube", "tiktok",
                           "alt_language_label", "alt_language_url", "contact_email", "tips_note"]),
        ("التذييل", ["footer_about", "copyright_text"]),
        ("سياسات التحرير والأمان", ["require_2fa_all_staff", "require_review"]),
        ("الجمهور والتوزيع", ["analytics_enabled", "analytics_respect_dnt", "newsletter_enabled", "newsletter_hour",
                              "newsletter_count", "push_enabled", "home_cache_seconds"]),
        ("متقدم", ["custom_head_html"]),
    )

    def sections(self):
        for title, names in self.SECTIONS:
            yield title, [self[n] for n in names if n in self.fields]


class ChannelConfigForm(forms.ModelForm):
    auto_kinds = forms.MultipleChoiceField(
        label="أنواع المواد التي تُرسل تلقائياً",
        choices=ArticleKind.choices,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="لا تحدد شيئاً لإرسال كل الأنواع (وفق خيارات كل مادة).",
    )

    class Meta:
        model = ChannelConfig
        fields = ["enabled", "auto_kinds", "breaking_only", "telegram_chat_ids", "with_image", "silent_hours",
                  "whatsapp_template", "whatsapp_language", "message_template"]
        widgets = {"message_template": forms.Textarea(attrs={"rows": 4, "dir": "auto"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        channel = self.instance.channel
        hide = {
            "telegram": {"whatsapp_template", "whatsapp_language"},
            "whatsapp": {"telegram_chat_ids", "with_image", "silent_hours"},
            "push": {"telegram_chat_ids", "with_image", "silent_hours", "whatsapp_template", "whatsapp_language", "message_template"},
            "newsletter": {"telegram_chat_ids", "with_image", "silent_hours", "whatsapp_template", "whatsapp_language",
                           "message_template", "auto_kinds", "breaking_only"},
        }.get(channel, set())
        for name in hide:
            self.fields.pop(name, None)


class WhatsAppSubscriberForm(forms.ModelForm):
    class Meta:
        model = WhatsAppSubscriber
        fields = ["phone", "name", "is_active"]


class MediaMetaForm(forms.ModelForm):
    class Meta:
        model = MediaAsset
        fields = ["title", "caption", "credit", "alt_text"]
