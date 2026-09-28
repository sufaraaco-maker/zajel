"""المحتوى: الأقسام، الوسوم، الكتّاب، المواد بأنواعها، الملفات الخاصة، العاجل،
التغطيات المباشرة، الصفحات الثابتة، مكتبة الوسائط، والمراجعات."""

from __future__ import annotations

import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

from arcms.arabic.normalize import normalize
from arcms.arabic.text import arabic_slugify, reading_minutes
from arcms.core.fields import EncryptedTextField


def _unique_slug(model, base: str, instance_pk=None, field: str = "slug") -> str:
    base = arabic_slugify(base) or uuid.uuid4().hex[:8]
    slug, n = base, 2
    qs = model._base_manager.all()
    if instance_pk:
        qs = qs.exclude(pk=instance_pk)
    while qs.filter(**{field: slug}).exists():
        slug = f"{base}-{n}"
        n += 1
    return slug


class Category(models.Model):
    name = models.CharField("الاسم", max_length=80)
    slug = models.SlugField("الرابط", max_length=100, unique=True, allow_unicode=True, blank=True)
    parent = models.ForeignKey(
        "self", verbose_name="القسم الأب", null=True, blank=True, on_delete=models.PROTECT, related_name="children"
    )
    description = models.TextField("الوصف", blank=True)
    color = models.CharField("لون القسم", max_length=7, blank=True)
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("مفعّل", default=True)
    desk_members = models.ManyToManyField(
        settings.AUTH_USER_MODEL, verbose_name="طاقم القسم", blank=True, related_name="desks"
    )
    legacy_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["order", "name"]
        verbose_name = "قسم"
        verbose_name_plural = "الأقسام"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Category, self.name, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:category", args=[self.slug])

    def family_ids(self) -> list[int]:
        return [self.pk, *self.children.values_list("id", flat=True)]


class Tag(models.Model):
    name = models.CharField("الوسم", max_length=80)
    slug = models.SlugField(max_length=100, unique=True, allow_unicode=True, blank=True)
    normalized = models.CharField(max_length=80, unique=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name = "وسم"
        verbose_name_plural = "الوسوم"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.name = self.name.strip().lstrip("#").strip()
        # «الأسرى» و«الاسرى» وسم واحد.
        self.normalized = normalize(self.name.replace("_", " "))
        if not self.slug:
            self.slug = _unique_slug(Tag, self.name.replace(" ", "_"), self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:tag", args=[self.slug])

    @classmethod
    def get_or_create_by_name(cls, name: str) -> "Tag":
        name = name.strip().lstrip("#").strip()
        key = normalize(name.replace("_", " "))
        tag = cls.objects.filter(normalized=key).first()
        return tag or cls.objects.create(name=name)


class Author(models.Model):
    """اسم في سطر الكاتب. قد يكون صحفياً في الطاقم أو كاتب رأي من خارجه."""

    name = models.CharField("الاسم", max_length=120)
    slug = models.SlugField(max_length=140, unique=True, allow_unicode=True, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        verbose_name="حساب الطاقم",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="author_profile",
    )
    title = models.CharField("الصفة", max_length=120, blank=True, help_text="مثل: مراسل، كاتب وباحث")
    bio = models.TextField("نبذة", blank=True)
    photo = models.ForeignKey(
        "MediaAsset", verbose_name="الصورة", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    x_handle = models.CharField("حساب إكس", max_length=60, blank=True)
    is_columnist = models.BooleanField("كاتب رأي", default=False)
    show_page = models.BooleanField("صفحة عامة", default=True)
    legacy_id = models.CharField(max_length=64, blank=True, db_index=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "كاتب"
        verbose_name_plural = "الكتّاب"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Author, self.name, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:author", args=[self.slug])


class MediaAsset(models.Model):
    """صورة في المكتبة. تُنزع بياناتها المخفية عند الرفع، ويُحفظ ملخص ما نُزع."""

    class Kind(models.TextChoices):
        IMAGE = "image", "صورة"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.IMAGE)
    file = models.FileField("الملف", upload_to="uploads/%Y/%m/", max_length=255)
    title = models.CharField("العنوان", max_length=200, blank=True)
    alt_text = models.CharField("النص البديل", max_length=250, blank=True)
    caption = models.CharField("التعليق", max_length=400, blank=True)
    credit = models.CharField("المصدر/المصوّر", max_length=150, blank=True)
    width = models.PositiveIntegerField(default=0)
    height = models.PositiveIntegerField(default=0)
    mime = models.CharField(max_length=60, blank=True)
    size = models.PositiveIntegerField(default=0)
    sha256 = models.CharField(max_length=64, db_index=True, blank=True)
    renditions = models.JSONField(default=dict, blank=True)
    removed_metadata = models.JSONField("البيانات المنزوعة", default=list, blank=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    legacy_url = models.URLField(max_length=500, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "وسيط"
        verbose_name_plural = "مكتبة الوسائط"

    def __str__(self) -> str:
        return self.title or self.caption or f"صورة {self.pk}"

    @property
    def url(self) -> str:
        return self.file.url if self.file else ""

    def rendition(self, name: str) -> str:
        path = (self.renditions or {}).get(name)
        if path:
            from django.core.files.storage import default_storage

            return default_storage.url(path)
        return self.url

    @property
    def thumb(self) -> str:
        return self.rendition("thumb")

    @property
    def card(self) -> str:
        return self.rendition("card")

    @property
    def large(self) -> str:
        return self.rendition("large")

    @property
    def social(self) -> str:
        return self.rendition("social")

    @property
    def srcset(self) -> str:
        parts = []
        for name, width in (("thumb", 400), ("card", 800), ("large", 1400)):
            if name in (self.renditions or {}):
                parts.append(f"{self.rendition(name)} {width}w")
        return ", ".join(parts)


class Dossier(models.Model):
    """ملف خاص يجمع مواد حول قضية واحدة (مثل «الأسرى» أو «الحرب على غزة»)."""

    title = models.CharField("العنوان", max_length=150)
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=True, blank=True)
    description = models.TextField("التقديم", blank=True)
    cover = models.ForeignKey(MediaAsset, verbose_name="الغلاف", null=True, blank=True, on_delete=models.SET_NULL)
    color = models.CharField("اللون", max_length=7, blank=True)
    is_active = models.BooleanField("مفعّل", default=True)
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["order", "-created_at"]
        verbose_name = "ملف خاص"
        verbose_name_plural = "الملفات الخاصة"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Dossier, self.title, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:dossier", args=[self.slug])


class ArticleKind(models.TextChoices):
    NEWS = "news", "خبر"
    REPORT = "report", "تقرير"
    INVESTIGATION = "investigation", "تحقيق"
    INTERVIEW = "interview", "حوار"
    OPINION = "opinion", "مقال رأي"
    BLOG = "blog", "تدوينة"
    TRANSLATION = "translation", "ترجمة"
    VIDEO = "video", "فيديو"
    INFOGRAPHIC = "infographic", "إنفوجراف"
    GALLERY = "gallery", "معرض صور"
    CARTOON = "cartoon", "كاريكاتير"


KIND_PLURALS = {
    "news": "أخبار",
    "report": "تقارير",
    "investigation": "تحقيقات",
    "interview": "حوارات",
    "opinion": "مقالات",
    "blog": "مدونات",
    "translation": "ترجمات",
    "video": "فيديو",
    "infographic": "إنفوجراف",
    "gallery": "صور",
    "cartoon": "كاريكاتير",
}


class Status(models.TextChoices):
    DRAFT = "draft", "مسودة"
    IN_REVIEW = "in_review", "بانتظار المراجعة"
    CHANGES = "changes", "أُعيدت للتعديل"
    APPROVED = "approved", "معتمدة للنشر"
    SCHEDULED = "scheduled", "مجدولة"
    PUBLISHED = "published", "منشورة"
    UNPUBLISHED = "unpublished", "مسحوبة"


class ArticleQuerySet(models.QuerySet):
    def published(self):
        return self.filter(status=Status.PUBLISHED, published_at__lte=timezone.now())

    def public_list(self):
        return (
            self.published()
            .select_related("category", "featured_image")
            .prefetch_related("authors")
            .order_by("-published_at")
        )


class Article(models.Model):
    kind = models.CharField("نوع المادة", max_length=20, choices=ArticleKind.choices, default=ArticleKind.NEWS)
    status = models.CharField("الحالة", max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)

    kicker = models.CharField("سطر فوق العنوان", max_length=60, blank=True, help_text="مثل: خاص، فيديو، ترجمة")
    title = models.CharField("العنوان", max_length=250)
    subtitle = models.CharField("العنوان الفرعي", max_length=300, blank=True)
    excerpt = models.TextField("الملخص", blank=True, help_text="يظهر في القوائم ونتائج البحث والمشاركة.")
    dateline = models.CharField("مكان الخبر", max_length=80, blank=True, help_text="مثل: غزة - خاص")
    body = models.TextField("المتن", blank=True)
    slug = models.CharField("الرابط", max_length=120, blank=True, db_index=True)

    category = models.ForeignKey(
        Category, verbose_name="القسم", on_delete=models.PROTECT, related_name="articles", null=True, blank=True
    )
    extra_categories = models.ManyToManyField(
        Category, verbose_name="أقسام إضافية", blank=True, related_name="extra_articles"
    )
    tags = models.ManyToManyField(Tag, verbose_name="الوسوم", blank=True, related_name="articles")
    authors = models.ManyToManyField(Author, verbose_name="الكتّاب", blank=True, related_name="articles")
    dossiers = models.ManyToManyField(Dossier, verbose_name="الملفات الخاصة", blank=True, related_name="articles")
    related = models.ManyToManyField("self", verbose_name="مواد ذات صلة", blank=True, symmetrical=False)

    featured_image = models.ForeignKey(
        MediaAsset, verbose_name="الصورة الرئيسية", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    image_caption = models.CharField("تعليق الصورة", max_length=400, blank=True)
    hide_featured_image = models.BooleanField("إخفاء الصورة في صفحة المادة", default=False)
    video_url = models.URLField("رابط الفيديو", blank=True, help_text="يوتيوب أو فيميو أو فيسبوك")
    gallery = models.ManyToManyField(MediaAsset, through="GalleryItem", blank=True, related_name="galleries")

    source = models.CharField("المصدر", max_length=150, blank=True)
    source_url = models.URLField("رابط المصدر", blank=True)
    source_notes = EncryptedTextField(
        "ملاحظات المصادر (سرّية ومشفّرة)",
        blank=True,
        help_text="لا تظهر للجمهور أبداً، ولا يراها إلا كاتب المادة ورئيس التحرير.",
    )
    correction = models.TextField("تصحيح منشور", blank=True, help_text="يظهر أسفل المادة إن وُجد.")

    is_breaking = models.BooleanField("عاجل", default=False)
    is_featured = models.BooleanField("في الواجهة", default=False)
    is_exclusive = models.BooleanField("خاص", default=False)
    priority = models.SmallIntegerField("الأولوية", default=0, help_text="الأعلى يتقدّم في الواجهة.")
    featured_until = models.DateTimeField("في الواجهة حتى", null=True, blank=True)
    allow_indexing = models.BooleanField("السماح لمحركات البحث", default=True)
    seo_title = models.CharField("عنوان محركات البحث", max_length=200, blank=True)
    seo_description = models.CharField("وصف محركات البحث", max_length=300, blank=True)

    send_telegram = models.BooleanField("تيليجرام", default=True)
    send_whatsapp = models.BooleanField("واتساب", default=False)
    send_push = models.BooleanField("إشعار فوري", default=False)
    in_newsletter = models.BooleanField("في النشرة اليومية", default=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="أنشأها", null=True, on_delete=models.SET_NULL, related_name="articles"
    )
    last_edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField("أُنشئت", default=timezone.now)
    updated_at = models.DateTimeField("آخر حفظ", auto_now=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    scheduled_at = models.DateTimeField("موعد النشر", null=True, blank=True, db_index=True)
    published_at = models.DateTimeField("تاريخ النشر", null=True, blank=True, db_index=True)
    first_published_at = models.DateTimeField(null=True, blank=True)
    content_updated_at = models.DateTimeField("آخر تحديث للمحتوى", null=True, blank=True)
    distributed_at = models.DateTimeField(null=True, blank=True)

    view_count = models.PositiveIntegerField("المشاهدات", default=0)
    word_count = models.PositiveIntegerField(default=0)
    legacy_id = models.CharField(max_length=64, blank=True, db_index=True)
    legacy_url = models.CharField(max_length=500, blank=True)

    objects = ArticleQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at", "-created_at"]
        verbose_name = "مادة"
        verbose_name_plural = "المواد"
        indexes = [
            models.Index(fields=["status", "-published_at"]),
            models.Index(fields=["kind", "status", "-published_at"]),
            models.Index(fields=["category", "status", "-published_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = arabic_slugify(self.title) or "مادة"
        text = strip_tags(self.body or "")
        self.word_count = len(text.split())
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:article", args=[self.pk, self.slug or "-"])

    def get_short_url(self) -> str:
        return reverse("public:short", args=[self.pk])

    @property
    def is_live(self) -> bool:
        return self.status == Status.PUBLISHED and self.published_at and self.published_at <= timezone.now()

    @property
    def reading_minutes(self) -> int:
        return reading_minutes(strip_tags(self.body or ""))

    @property
    def kind_label(self) -> str:
        return self.get_kind_display()

    @property
    def display_kicker(self) -> str:
        if self.kicker:
            return self.kicker
        if self.is_exclusive:
            return "خاص"
        if self.kind in (ArticleKind.NEWS,):
            return ""
        return self.get_kind_display()

    @property
    def was_updated(self) -> bool:
        return bool(
            self.content_updated_at
            and self.published_at
            and self.content_updated_at - self.published_at > timedelta(minutes=10)
        )

    @property
    def summary(self) -> str:
        if self.excerpt:
            return self.excerpt
        import re

        spaced = re.sub(r"</(p|h\d|li|div|blockquote|figcaption)>|<br\s*/?>", " ", self.body or "")
        text = " ".join(strip_tags(spaced).split())
        return text[:220] + ("…" if len(text) > 220 else "")

    @property
    def primary_author(self):
        authors = list(self.authors.all())
        return authors[0] if authors else None

    def video_embed(self) -> str:
        from .embeds import video_embed_url

        return video_embed_url(self.video_url)


class GalleryItem(models.Model):
    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="gallery_items")
    media = models.ForeignKey(MediaAsset, on_delete=models.CASCADE)
    caption = models.CharField(max_length=400, blank=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]


class ArticleRevision(models.Model):
    """لقطة من المادة عند كل حفظ؛ تتيح المقارنة والاسترجاع."""

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="revisions")
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices)
    data = models.JSONField()
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    SNAPSHOT_FIELDS = ("kicker", "title", "subtitle", "excerpt", "dateline", "body", "image_caption", "correction")

    @classmethod
    def capture(cls, article: Article, user=None, note: str = "") -> "ArticleRevision":
        data = {f: getattr(article, f) for f in cls.SNAPSHOT_FIELDS}
        last = article.revisions.first()
        if last and last.data == data:
            return last
        return cls.objects.create(article=article, created_by=user, status=article.status, data=data, note=note)


class EditorialNote(models.Model):
    """حوار داخلي بين الكاتب والمحرر حول المادة (لا يظهر للجمهور)."""

    article = models.ForeignKey(Article, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    body = models.TextField()
    kind = models.CharField(max_length=20, default="comment")  # comment | return | approve | system
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["created_at"]


class ArticleLock(models.Model):
    """قفل تحرير يمنع محررَين من الكتابة فوق عمل بعضهما."""

    article = models.OneToOneField(Article, on_delete=models.CASCADE, related_name="lock")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    heartbeat_at = models.DateTimeField(default=timezone.now)

    TTL = timedelta(seconds=90)

    @property
    def is_stale(self) -> bool:
        return timezone.now() - self.heartbeat_at > self.TTL


class BreakingNews(models.Model):
    text = models.CharField("نص العاجل", max_length=280)
    article = models.ForeignKey(
        Article, verbose_name="المادة المرتبطة", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    link = models.CharField("رابط", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField("ينتهي", null=True, blank=True)
    is_active = models.BooleanField("ظاهر", default=True)
    send_telegram = models.BooleanField("تيليجرام", default=True)
    send_push = models.BooleanField("إشعار فوري", default=True)
    send_whatsapp = models.BooleanField("واتساب", default=False)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "خبر عاجل"
        verbose_name_plural = "العاجل"

    def __str__(self) -> str:
        return self.text

    def get_url(self) -> str:
        if self.article_id:
            return self.article.get_absolute_url()
        from arcms.core.utils import is_safe_link

        return self.link if is_safe_link(self.link) else ""

    @classmethod
    def current(cls, hours: int = 12):
        now = timezone.now()
        return (
            cls.objects.filter(is_active=True, created_at__gte=now - timedelta(hours=hours))
            .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
            .select_related("article")
        )


class LiveCoverage(models.Model):
    title = models.CharField("العنوان", max_length=200)
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=True, blank=True)
    summary = models.TextField("التقديم", blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="القسم")
    cover = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="الغلاف")
    is_live = models.BooleanField("جارية الآن", default=True)
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-started_at"]
        verbose_name = "تغطية مباشرة"
        verbose_name_plural = "التغطيات المباشرة"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(LiveCoverage, self.title, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:live", args=[self.slug])


class LiveEntry(models.Model):
    coverage = models.ForeignKey(LiveCoverage, on_delete=models.CASCADE, related_name="entries")
    body = models.TextField("النص")
    image = models.ForeignKey(MediaAsset, null=True, blank=True, on_delete=models.SET_NULL)
    is_important = models.BooleanField("مهم", default=False)
    is_pinned = models.BooleanField("مثبّت", default=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Page(models.Model):
    title = models.CharField("العنوان", max_length=150)
    slug = models.SlugField(max_length=160, unique=True, allow_unicode=True, blank=True)
    body = models.TextField("المحتوى", blank=True)
    is_published = models.BooleanField("منشورة", default=True)
    show_contact_form = models.BooleanField("إظهار نموذج التواصل", default=False)
    order = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "title"]
        verbose_name = "صفحة"
        verbose_name_plural = "الصفحات الثابتة"

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _unique_slug(Page, self.title, self.pk)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("public:page", args=[self.slug])


class ContactMessage(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    subject = models.CharField(max_length=200, blank=True)
    body = EncryptedTextField()
    created_at = models.DateTimeField(default=timezone.now)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]


class SearchDocument(models.Model):
    """النص المحلَّل لكل مادة. الفهرس الفعلي (tsvector أو FTS5) يُبنى منه."""

    article = models.OneToOneField(Article, on_delete=models.CASCADE, primary_key=True, related_name="search_doc")
    title_terms = models.TextField(blank=True)
    lead_terms = models.TextField(blank=True)
    body_terms = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
