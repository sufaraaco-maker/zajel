from __future__ import annotations

from django import forms
from django.utils import timezone

from arcms.accounts.roles import Cap
from arcms.arabic import style
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
            "featured_image", "image_caption", "hide_featured_image", "video_url", "audio",
            "source", "source_url", "source_notes", "correction",
            "is_breaking", "is_featured", "is_exclusive", "priority", "featured_until",
            "allow_indexing", "seo_title", "seo_description",
            "send_telegram", "send_whatsapp", "send_push", "send_facebook", "send_x", "in_newsletter",
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
        self.fields["extra_categories"].queryset = cats
        self.fields["featured_image"].queryset = MediaAsset.objects.all()
        self.fields["audio"].widget.attrs["accept"] = "audio/mpeg,audio/mp4,audio/ogg,.mp3,.m4a,.ogg"
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

    def clean_audio(self):
        from django.core.files.base import ContentFile
        from django.core.files.uploadedfile import UploadedFile

        from arcms.content.audio import AudioRejected, clean_audio

        value = self.cleaned_data.get("audio")
        if not isinstance(value, UploadedFile):
            return value  # لم يُرفع جديد (أو طُلب الحذف)
        try:
            clean = clean_audio(value.read())
        except AudioRejected as exc:
            raise forms.ValidationError(str(exc)) from exc
        self.audio_report = clean.removed, clean.warning
        return ContentFile(clean.content, name=clean.filename)

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


def _clean_link(value: str) -> str:
    from arcms.core.utils import is_safe_link

    value = (value or "").strip()
    if value and not is_safe_link(value):
        raise forms.ValidationError("الرابط يجب أن يبدأ بـ https:// أو http:// أو / (مسار داخل الموقع).")
    return value


class BreakingForm(forms.ModelForm):
    class Meta:
        model = BreakingNews
        fields = ["text", "article", "link", "expires_at", "send_telegram", "send_push", "send_whatsapp", "send_x"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 2, "maxlength": 280, "autofocus": True}),
            "article": forms.HiddenInput(),
            "expires_at": DateTimeLocal(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # العاجل يُعرض للقراء ويُرسل للقنوات: لا يُربط إلا بمادة منشورة، وإلا كشف رابطُه عنوانَ مسودة.
        self.fields["article"].queryset = Article.objects.published()

    def clean_link(self):
        return _clean_link(self.cleaned_data.get("link"))


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
        fields = ["name", "slug", "parent", "description", "color", "cover", "page_layout", "order", "is_active",
                  "desk_members"]
        widgets = {
            "color": forms.TextInput(attrs={"type": "color"}),
            "cover": forms.HiddenInput(),
            "description": forms.Textarea(attrs={"rows": 2}),
            "desk_members": forms.SelectMultiple(attrs={"size": 8}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["page_layout"].required = False

    def clean_page_layout(self):
        return self.cleaned_data.get("page_layout") or "feed"


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

    def clean_url(self):
        return _clean_link(self.cleaned_data.get("url"))


class HomeBlockForm(forms.ModelForm):
    # أي الحقول يخص أي نوع كتلة (ما لم يُذكر يظهر دائماً). يقرؤها سكربت النموذج لإخفاء ما لا يلزم.
    FIELD_KINDS = {
        "layout": "hero latest category kind video opinion picks",
        "count": "hero latest category columns most_read opinion video kind dossier brief picks",
        "category": "category brief",
        "categories": "columns",
        "dossier": "dossier",
        "article_kind": "kind video opinion",
        "ad_slot": "ad",
        "html": "html",
        "subtitle": "brief promo stats newsletter",
        "text": "brief promo stats newsletter",
        "link": "promo",
        "button_label": "promo",
        "image": "promo",
        "items": "stats platforms",
        "pick_ids": "picks",
    }

    pick_ids = forms.CharField(label="المواد المختارة", required=False, widget=forms.HiddenInput())

    class Meta:
        model = HomeBlock
        fields = ["kind", "title", "subtitle", "layout", "count", "category", "categories", "dossier", "article_kind",
                  "ad_slot", "text", "link", "button_label", "image", "items", "html", "background", "order", "is_active"]
        widgets = {
            "html": forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
            "categories": forms.SelectMultiple(attrs={"size": 6}),
            "text": forms.Textarea(attrs={"rows": 3}),
            "items": forms.Textarea(attrs={"rows": 5}),
            "image": forms.HiddenInput(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _lock_raw_html(self, user)
        self.fields["article_kind"] = forms.ChoiceField(
            label="نوع المادة", choices=[("", "—")] + list(ArticleKind.choices), required=False
        )
        if self.instance.pk:
            self.fields["pick_ids"].initial = ",".join(
                str(i) for i in self.instance.homeblockarticle_set.values_list("article_id", flat=True)
            )

    def clean_link(self):
        return _clean_link(self.cleaned_data.get("link"))

    def picks(self) -> list:
        ids = [int(i) for i in (self["pick_ids"].value() or "").split(",") if i.strip().isdigit()]
        by_id = {a.pk: a for a in Article.objects.filter(pk__in=ids)}
        return [by_id[i] for i in ids if i in by_id]

    def save(self, commit=True):
        block = super().save(commit=commit)
        if commit:
            from arcms.core.models import HomeBlockArticle

            ids = [int(i) for i in (self.cleaned_data.get("pick_ids") or "").split(",") if i.strip().isdigit()]
            valid = set(Article.objects.published().filter(pk__in=ids).values_list("pk", flat=True))
            HomeBlockArticle.objects.filter(block=block).delete()
            HomeBlockArticle.objects.bulk_create(
                [HomeBlockArticle(block=block, article_id=i, order=n) for n, i in enumerate(dict.fromkeys(ids)) if i in valid]
            )
        return block


RAW_HTML_HELP = "الشيفرة الخام يضيفها مدير النظام وحده: سكربت من طرف ثالث يعمل على نطاق الموقع نفسه."


def _lock_raw_html(form, user) -> None:
    """حقل الشيفرة الخام لمدير النظام فقط؛ لغيره يبقى ما فيه كما هو ولا يُعدَّل."""
    if user is not None and not user.can(Cap.SETTINGS) and "html" in form.fields:
        form.fields["html"].disabled = True
        form.fields["html"].help_text = RAW_HTML_HELP


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

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        _lock_raw_html(self, user)


class SiteSettingsForm(forms.ModelForm):
    class Meta:
        model = SiteSettings
        # دليل الأسلوب له صفحته وصلاحيته؛ لو بقي هنا لمسحه حفظ الإعدادات لأن القالب لا يعرضه.
        exclude = ["updated_at", "style_rules", "style_disabled"]
        widgets = {
            "logo": forms.HiddenInput(),
            "logo_dark": forms.HiddenInput(),
            "default_share_image": forms.HiddenInput(),
            "primary_color": forms.TextInput(attrs={"type": "color", "data-brand": "primary"}),
            "accent_color": forms.TextInput(attrs={"type": "color", "data-brand": "accent"}),
            **{
                name: forms.TextInput(attrs={"data-color-optional": name, "placeholder": "تلقائي", "dir": "ltr",
                                             "maxlength": 7, "data-brand": name.replace("_color", "").replace("_mode", "")})
                for name in SiteSettings.COLOR_AREAS
            },
            "description": forms.Textarea(attrs={"rows": 2}),
            "footer_about": forms.Textarea(attrs={"rows": 3}),
            "custom_head_html": forms.Textarea(attrs={"rows": 4, "dir": "ltr"}),
        }

    COLOR_SECTION = "الألوان والهوية البصرية"
    SECTIONS = (
        ("الهوية", ["name", "short_name", "tagline", "description", "logo", "logo_dark", "logo_height",
                    "default_share_image"]),
        (COLOR_SECTION, ["primary_color", "accent_color", "header_dark", *SiteSettings.COLOR_AREAS]),
        ("الخطوط والشكل", ["font_headings", "font_body", "corner_style"]),
        ("الترويسة والتذييل", ["header_style", "header_cta_label", "header_cta_url", "app_ios_url", "app_android_url"]),
        ("المشاركة على المنصات", ["share_cards"]),
        ("التاريخ والأرقام", ["month_style", "digits", "clock", "show_hijri", "hijri_adjust"]),
        ("شريط العاجل", ["ticker_enabled", "ticker_label", "ticker_hours"]),
        ("روابط التواصل", ["telegram", "whatsapp", "x_twitter", "facebook", "instagram", "youtube", "tiktok",
                           "alt_language_label", "alt_language_url", "contact_email", "tips_note"]),
        ("التذييل", ["footer_about", "copyright_text"]),
        ("سياسات التحرير والأمان", ["require_2fa_all_staff", "require_review", "tips_enabled"]),
        ("الجمهور والتوزيع", ["analytics_enabled", "analytics_respect_dnt", "newsletter_enabled", "newsletter_hour",
                              "newsletter_count", "push_enabled", "home_cache_seconds"]),
        ("متقدم", ["custom_head_html"]),
    )

    def clean_logo_height(self):
        value = self.cleaned_data.get("logo_height") or 0
        if 0 < value < 32:
            raise forms.ValidationError("أقل ارتفاع مقروء 32 بكسل (أو صفر للتلقائي).")
        return value

    def clean_header_cta_url(self):
        return _clean_link(self.cleaned_data.get("header_cta_url"))

    def clean(self):
        data = super().clean()
        for name in SiteSettings.COLOR_AREAS + ("primary_color", "accent_color"):
            value = (data.get(name) or "").strip().lower()
            if name in data:
                data[name] = value
        page = data.get("page_color")
        if page:
            from arcms.core.colors import is_light

            if not is_light(page):
                self.add_error("page_color", "خلفية الصفحة يجب أن تكون فاتحة (نص المقالات داكن). للوضع الداكن زر مستقل عند القارئ.")
        return data

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
        fields = ["title", "caption", "credit", "alt_text", "sensitive", "focal_x", "focal_y"]
        widgets = {"focal_x": forms.HiddenInput(), "focal_y": forms.HiddenInput()}

    def _clean_unit(self, name):
        value = self.cleaned_data.get(name)
        return 0.5 if value is None else min(1.0, max(0.0, value))

    def clean_focal_x(self):
        return self._clean_unit("focal_x")

    def clean_focal_y(self):
        return self._clean_unit("focal_y")


class WireSourceForm(forms.ModelForm):
    class Meta:
        from arcms.wires.models import WireSource

        model = WireSource
        fields = ["name", "feed_url", "credit", "category", "poll_minutes", "is_active"]
        widgets = {"feed_url": forms.URLInput(attrs={"dir": "ltr"})}

    def clean_poll_minutes(self):
        value = self.cleaned_data.get("poll_minutes") or 5
        if not 2 <= value <= 240:
            raise forms.ValidationError("بين دقيقتين و240 دقيقة.")
        return value

    def clean_feed_url(self):
        from arcms.wires.feeds import FeedError, check_url

        url = (self.cleaned_data.get("feed_url") or "").strip()
        try:
            check_url(url)
        except FeedError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return url


class AssignmentForm(forms.ModelForm):
    class Meta:
        from arcms.planning.models import Assignment

        model = Assignment
        fields = ["title", "brief", "category", "kind", "assignee", "due_at", "priority", "status"]
        widgets = {"brief": forms.Textarea(attrs={"rows": 4, "class": "sensitive"}), "due_at": DateTimeLocal()}

    def __init__(self, *args, **kwargs):
        from arcms.accounts.models import User
        from arcms.accounts.roles import ROLE_CAPABILITIES
        from arcms.planning.models import Assignment

        super().__init__(*args, **kwargs)
        writers = [r for r, caps in ROLE_CAPABILITIES.items() if Cap.ARTICLE_CREATE in caps]
        self.fields["assignee"].queryset = User.objects.filter(is_active=True, role__in=writers).order_by("display_name")
        self.fields["assignee"].required = False
        self.fields["kind"] = forms.ChoiceField(label="نوع المادة", choices=ArticleKind.choices, initial="news")
        linked = bool(self.instance.pk and self.instance.article_id)
        if linked:  # المرحلة تتبع المادة
            del self.fields["status"]
        else:
            self.fields["status"].choices = [(s, lbl) for s, lbl in Assignment.Status.choices
                                             if s in (Assignment.Status.IDEA, Assignment.Status.ASSIGNED,
                                                      Assignment.Status.DROPPED)]

    def clean(self):
        from arcms.planning.models import Assignment

        data = super().clean()
        status = data.get("status")
        if status == Assignment.Status.ASSIGNED and not data.get("assignee"):
            self.add_error("assignee", "اختر من تكلّفه، أو اجعل المرحلة «فكرة».")
        if status == Assignment.Status.IDEA and data.get("assignee"):
            data["status"] = Assignment.Status.ASSIGNED
        return data


class PollForm(forms.ModelForm):
    options_text = forms.CharField(
        label="الخيارات", widget=forms.Textarea(attrs={"rows": 5}),
        help_text="خيار في كل سطر (بين خيارين وثمانية). بعد بدء التصويت يمكن تعديل نص الخيار وإضافة خيارات، لا حذفها.",
    )

    class Meta:
        from arcms.polls.models import Poll

        model = Poll
        fields = ["question", "description", "is_open", "closes_at", "show_results_before_vote"]
        widgets = {"closes_at": DateTimeLocal()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["options_text"].initial = "\n".join(self.instance.options.values_list("text", flat=True))

    def clean_options_text(self):
        lines = [" ".join(line.split())[:150] for line in self.cleaned_data["options_text"].splitlines()]
        lines = [line for line in lines if line]
        if not 2 <= len(lines) <= 8:
            raise forms.ValidationError("اكتب بين خيارين وثمانية خيارات، كل خيار في سطر.")
        if len(set(lines)) != len(lines):
            raise forms.ValidationError("الخيارات مكررة.")
        if self.instance.pk and self.instance.total_votes and len(lines) < self.instance.options.count():
            raise forms.ValidationError("بدأ التصويت: لا يمكن حذف خيارات (عدّل نصها أو أضف جديدة).")
        return lines

    def save(self, commit=True):
        poll = super().save(commit=commit)
        if commit:
            self.sync_options(poll)
        return poll

    def sync_options(self, poll):
        """الخيارات بترتيب الأسطر: يُعدَّل نص الموجود ويُضاف الجديد، فتبقى الأصوات مع خياراتها."""
        from arcms.polls.models import PollOption

        existing = list(poll.options.all())
        for order, text in enumerate(self.cleaned_data["options_text"]):
            if order < len(existing):
                opt = existing[order]
                if (opt.text, opt.order) != (text, order):
                    opt.text, opt.order = text, order
                    opt.save(update_fields=["text", "order"])
            else:
                PollOption.objects.create(poll=poll, text=text, order=order)
        for extra in existing[len(self.cleaned_data["options_text"]):]:
            extra.delete()  # يصل هنا فقط إن لم يبدأ التصويت


class StyleGuideForm(forms.ModelForm):
    checks = forms.MultipleChoiceField(
        label="الفحوص المفعّلة", required=False, widget=forms.CheckboxSelectMultiple,
        choices=list(style.CHECKS.items()),
    )

    class Meta:
        model = SiteSettings
        fields = ["style_rules"]
        widgets = {"style_rules": forms.Textarea(attrs={"rows": 16, "dir": "rtl", "spellcheck": "false",
                                                        "placeholder": "مسئول* => مسؤول | نكتب الهمزة على الواو"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        disabled = set(self.instance.style_disabled or [])
        self.fields["checks"].initial = [k for k in style.CHECKS if k not in disabled]

    def clean_style_rules(self):
        value = self.cleaned_data["style_rules"].replace("\r\n", "\n")
        if len(value) > 60_000:
            raise forms.ValidationError("الدليل أطول من المسموح (60 ألف حرف).")
        self.rule_errors = style.parse_rules(value)[1]
        return value

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.style_disabled = [k for k in style.CHECKS if k not in set(self.cleaned_data.get("checks") or [])]
        if commit:
            obj.save(update_fields=["style_rules", "style_disabled", "updated_at"])
        return obj

