"""بنّاء الموقع: الهوية والإعدادات، القوائم، كتل الصفحة الرئيسية، المساحات الإعلانية،
وطابور المهام الخلفية (النشر المجدول والتوزيع والنشرة)."""

from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from arcms.arabic.dates import MONTH_STYLES, DateStyle

hex_color = RegexValidator(r"^#[0-9a-fA-F]{6}$", "اكتب اللون بصيغة ‎#RRGGBB‎")

FONT_CHOICES = (
    ("plex", "IBM Plex Sans Arabic (عصري)"),
    ("kufi", "Noto Kufi Arabic (كوفي عريض)"),
    ("naskh", "Noto Naskh Arabic (نسخ صحفي)"),
    ("cairo", "Cairo (هندسي حديث)"),
    ("tajawal", "Tajawal (خفيف واضح)"),
    ("almarai", "Almarai (بسيط للشاشات)"),
    ("amiri", "Amiri (نسخ كلاسيكي للمقالات)"),
)

# الخط واحتياطاته. كلها مستضافة على الخادم (static/fonts) بترخيص SIL OFL.
FONT_STACKS = {
    "plex": '"IBM Plex Sans Arabic","Segoe UI",Tahoma,sans-serif',
    "kufi": '"Noto Kufi Arabic","Segoe UI",Tahoma,sans-serif',
    "naskh": '"Noto Naskh Arabic","Traditional Arabic",serif',
    "cairo": 'Cairo,"Segoe UI",Tahoma,sans-serif',
    "tajawal": 'Tajawal,"Segoe UI",Tahoma,sans-serif',
    "almarai": 'Almarai,"Segoe UI",Tahoma,sans-serif',
    "amiri": 'Amiri,"Traditional Arabic",serif',
}


class SiteSettings(models.Model):
    """سجل وحيد يحمل هوية الموقع وسلوكه. يُعدَّل من «إعدادات الموقع»."""

    # الهوية
    name = models.CharField("اسم الموقع", max_length=120, default="زاجل الإخبارية")
    short_name = models.CharField("الاسم المختصر", max_length=40, default="زاجل")
    tagline = models.CharField("الشعار النصي", max_length=200, default="الصورة الكاملة، من الميدان")
    description = models.TextField(
        "وصف الموقع (لمحركات البحث)", blank=True, default="شبكة إخبارية عربية مستقلة تغطي الأحداث على مدار الساعة."
    )
    logo = models.ForeignKey(
        "content.MediaAsset", verbose_name="الشعار", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    logo_dark = models.ForeignKey(
        "content.MediaAsset",
        verbose_name="الشعار للوضع الداكن",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    default_share_image = models.ForeignKey(
        "content.MediaAsset",
        verbose_name="صورة المشاركة الافتراضية",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    primary_color = models.CharField("اللون الرئيسي", max_length=7, default="#b0101c", validators=[hex_color])
    accent_color = models.CharField("لون التمييز", max_length=7, default="#111418", validators=[hex_color])
    header_dark = models.BooleanField("ترويسة داكنة", default=False)
    font_headings = models.CharField("خط العناوين", max_length=10, choices=FONT_CHOICES, default="plex")
    font_body = models.CharField("خط المتن", max_length=10, choices=FONT_CHOICES, default="naskh")

    # التاريخ واللغة
    month_style = models.CharField("أسماء الأشهر", max_length=10, choices=MONTH_STYLES, default="dual")
    digits = models.CharField(
        "رسم الأرقام", max_length=10, choices=(("latin", "0123456789"), ("arabic", "٠١٢٣٤٥٦٧٨٩")), default="latin"
    )
    clock = models.CharField("نظام الساعة", max_length=2, choices=(("12", "12 ساعة"), ("24", "24 ساعة")), default="12")
    show_hijri = models.BooleanField("إظهار التاريخ الهجري", default=True)
    hijri_adjust = models.SmallIntegerField("تعديل الهجري (أيام)", default=0)

    # الشريط العاجل
    ticker_enabled = models.BooleanField("تفعيل شريط العاجل", default=True)
    ticker_label = models.CharField("عنوان الشريط", max_length=20, default="عاجل")
    ticker_hours = models.PositiveSmallIntegerField("مدة بقاء العاجل (ساعات)", default=12)

    # التواصل والروابط
    facebook = models.URLField("فيسبوك", blank=True)
    x_twitter = models.URLField("إكس (تويتر)", blank=True)
    instagram = models.URLField("إنستغرام", blank=True)
    youtube = models.URLField("يوتيوب", blank=True)
    telegram = models.URLField("تيليجرام", blank=True)
    whatsapp = models.URLField("قناة واتساب", blank=True)
    tiktok = models.URLField("تيك توك", blank=True)
    alt_language_label = models.CharField("رابط لغة أخرى (النص)", max_length=30, blank=True, default="English")
    alt_language_url = models.URLField("رابط لغة أخرى", blank=True)
    contact_email = models.EmailField("بريد التواصل", blank=True)
    tips_note = models.CharField(
        "ملاحظة لمرسلي المعلومات",
        max_length=300,
        blank=True,
        default="لإرسال معلومة أو صورة بأمان، تواصل معنا عبر تيليجرام أو سيغنال. ننزع البيانات المخفية من كل صورة قبل نشرها.",
    )
    footer_about = models.TextField("نبذة التذييل", blank=True, default="")
    copyright_text = models.CharField("نص الحقوق", max_length=200, blank=True, default="جميع الحقوق محفوظة")

    # السلوك
    require_2fa_all_staff = models.BooleanField(
        "فرض التحقق الثنائي على كل الطاقم",
        default=True,
        help_text="مفروض دائماً على المحررين ومن فوقهم؛ هذا الخيار يمدّه إلى المراسلين والكتّاب.",
    )
    require_review = models.BooleanField(
        "إلزام المراجعة قبل النشر", default=True, help_text="يمنع رئيس القسم من نشر مادته دون مراجعة زميل."
    )
    header_style = models.CharField(
        "شكل الترويسة",
        max_length=10,
        choices=[("classic", "كلاسيكي: الشعار ثم شريط قائمة ملوّن"), ("compact", "حديث: الشعار والقائمة في سطر واحد")],
        default="classic",
    )
    header_cta_label = models.CharField("زر في الترويسة", max_length=40, blank=True, help_text="مثل: أرسل خبراً، تبرّع، البث المباشر")
    header_cta_url = models.CharField("رابط الزر", max_length=300, blank=True)
    corner_style = models.CharField(
        "زوايا البطاقات", max_length=10, choices=[("sharp", "حادة"), ("soft", "مدوّرة قليلاً"), ("round", "مدوّرة")],
        default="soft",
    )
    app_ios_url = models.URLField("تطبيق iPhone (App Store)", blank=True)
    app_android_url = models.URLField("تطبيق Android (Google Play)", blank=True)
    tips_enabled = models.BooleanField(
        "صندوق المعلومات الآمن",
        default=True,
        help_text="صفحة يرسل منها المصادر معلومات وصوراً دون كشف هويتهم، ويطّلع عليها رئيس التحرير ومدير النظام فقط.",
    )
    analytics_enabled = models.BooleanField("قياس الجمهور", default=True)
    analytics_respect_dnt = models.BooleanField("احترام «عدم التتبع» في المتصفح", default=True)
    newsletter_enabled = models.BooleanField("النشرة اليومية", default=True)
    newsletter_hour = models.PositiveSmallIntegerField("ساعة إرسال النشرة", default=7)
    newsletter_count = models.PositiveSmallIntegerField("عدد مواد النشرة", default=10)
    push_enabled = models.BooleanField("الإشعارات الفورية في المتصفح", default=True)
    home_cache_seconds = models.PositiveSmallIntegerField("تخزين الرئيسية مؤقتاً (ثوانٍ)", default=30)
    custom_head_html = models.TextField(
        "شيفرة إضافية في الترويسة",
        blank=True,
        help_text="لأكواد التحقق أو الإعلانات. تُضاف كما هي؛ لا تضع هنا أي متتبع لطرف ثالث إلا عن قصد.",
    )

    updated_at = models.DateTimeField(auto_now=True)

    CACHE_KEY = "arcms:site-settings"

    class Meta:
        verbose_name = "إعدادات الموقع"
        verbose_name_plural = "إعدادات الموقع"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CACHE_KEY)

    @classmethod
    def load(cls) -> "SiteSettings":
        obj = cache.get(cls.CACHE_KEY)
        if obj is None:
            obj, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CACHE_KEY, obj, 300)
        return obj

    @property
    def date_style(self) -> DateStyle:
        return DateStyle(
            months=self.month_style,
            digits=self.digits,
            clock=self.clock,
            hijri=self.show_hijri,
            hijri_adjust=self.hijri_adjust,
        )

    @property
    def heading_font(self) -> str:
        return FONT_STACKS.get(self.font_headings, FONT_STACKS["plex"])

    @property
    def heading_font_file(self) -> str:
        files = {"plex": "ibm-plex-sans-arabic", "kufi": "noto-kufi-arabic", "naskh": "noto-naskh-arabic"}
        return f"fonts/{files.get(self.font_headings, self.font_headings)}-arabic-700-normal.woff2"

    @property
    def body_font(self) -> str:
        return FONT_STACKS.get(self.font_body, FONT_STACKS["naskh"])

    def safe_cta_url(self) -> str:
        from arcms.core.utils import is_safe_link

        return self.header_cta_url if is_safe_link(self.header_cta_url) else ""

    @property
    def social_links(self) -> list[tuple[str, str, str]]:
        items = [
            ("telegram", "تيليجرام", self.telegram),
            ("whatsapp", "واتساب", self.whatsapp),
            ("x", "إكس", self.x_twitter),
            ("facebook", "فيسبوك", self.facebook),
            ("instagram", "إنستغرام", self.instagram),
            ("youtube", "يوتيوب", self.youtube),
            ("tiktok", "تيك توك", self.tiktok),
        ]
        return [i for i in items if i[2]]


class MenuItem(models.Model):
    class Location(models.TextChoices):
        MAIN = "main", "القائمة الرئيسية"
        TOP = "top", "الشريط العلوي"
        FOOTER = "footer", "التذييل"

    class LinkType(models.TextChoices):
        CATEGORY = "category", "قسم"
        KIND = "kind", "نوع مادة"
        DOSSIER = "dossier", "ملف خاص"
        PAGE = "page", "صفحة ثابتة"
        TAG = "tag", "وسم"
        LIVE = "live", "التغطيات المباشرة"
        URL = "url", "رابط"

    location = models.CharField("الموقع", max_length=10, choices=Location.choices, default=Location.MAIN)
    label = models.CharField("النص", max_length=60)
    link_type = models.CharField("نوع الرابط", max_length=10, choices=LinkType.choices, default=LinkType.CATEGORY)
    category = models.ForeignKey("content.Category", null=True, blank=True, on_delete=models.CASCADE)
    dossier = models.ForeignKey("content.Dossier", null=True, blank=True, on_delete=models.CASCADE)
    page = models.ForeignKey("content.Page", null=True, blank=True, on_delete=models.CASCADE)
    tag = models.ForeignKey("content.Tag", null=True, blank=True, on_delete=models.CASCADE)
    kind = models.CharField("نوع المادة", max_length=20, blank=True)
    url = models.CharField("الرابط", max_length=300, blank=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.CASCADE, related_name="children", verbose_name="تحت عنصر"
    )
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    highlight = models.BooleanField("تمييز بلون", default=False)
    new_tab = models.BooleanField("فتح في نافذة جديدة", default=False)
    is_active = models.BooleanField("مفعّل", default=True)

    class Meta:
        ordering = ["location", "order", "id"]
        verbose_name = "عنصر قائمة"
        verbose_name_plural = "القوائم"

    def __str__(self) -> str:
        return self.label

    def get_url(self) -> str:
        from django.urls import reverse

        try:
            if self.link_type == self.LinkType.CATEGORY and self.category_id:
                return self.category.get_absolute_url()
            if self.link_type == self.LinkType.DOSSIER and self.dossier_id:
                return self.dossier.get_absolute_url()
            if self.link_type == self.LinkType.PAGE and self.page_id:
                return self.page.get_absolute_url()
            if self.link_type == self.LinkType.TAG and self.tag_id:
                return self.tag.get_absolute_url()
            if self.link_type == self.LinkType.KIND and self.kind:
                return reverse("public:kind", args=[self.kind])
            if self.link_type == self.LinkType.LIVE:
                return reverse("public:live_list")
        except Exception:  # noqa: BLE001 - رابط مكسور لا يُسقط الصفحة
            return "#"
        from arcms.core.utils import is_safe_link

        return self.url if is_safe_link(self.url) else "#"


class HomeBlock(models.Model):
    """كتلة في الصفحة الرئيسية. ترتيبها وإعداداتها تصنع شكل الموقع."""

    class Kind(models.TextChoices):
        HERO = "hero", "الواجهة (خبر رئيسي وأخبار جانبية)"
        LATEST = "latest", "آخر الأخبار"
        CATEGORY = "category", "قسم"
        COLUMNS = "columns", "أعمدة أقسام متجاورة"
        MOST_READ = "most_read", "الأكثر قراءة"
        OPINION = "opinion", "مقالات الرأي"
        VIDEO = "video", "شريط الفيديو"
        KIND = "kind", "نوع مادة (إنفوجراف، تقارير...)"
        DOSSIER = "dossier", "ملف خاص"
        LIVE = "live", "التغطية المباشرة الجارية"
        BRIEF = "brief", "موجز الأخبار (صوتي ونصي)"
        PICKS = "picks", "مختارات المحررين"
        PROMO = "promo", "بطاقة ترويجية (قناة، تطبيق، حملة) مع رمز QR"
        STATS = "stats", "أرقام وإحصاءات"
        PLATFORMS = "platforms", "منصاتنا على التواصل"
        NEWSLETTER = "newsletter", "الاشتراك في النشرة"
        AD = "ad", "مساحة إعلانية"
        HTML = "html", "محتوى حر"

    class Layout(models.TextChoices):
        GRID = "grid", "شبكة بطاقات"
        FEATURE = "feature", "مادة كبيرة وقائمة"
        LIST = "list", "قائمة مضغوطة"
        STRIP = "strip", "شريط أفقي"
        CAROUSEL = "carousel", "شريط متحرك (بطاقات)"
        OVERLAY = "overlay", "شريط متحرك (العنوان على الصورة)"
        REELS = "reels", "فيديو عمودي قصير"

    class Background(models.TextChoices):
        NONE = "", "بلا خلفية"
        MUTED = "muted", "رمادية فاتحة"
        DARK = "dark", "داكنة"
        PRIMARY = "primary", "بلون الموقع"

    kind = models.CharField("نوع الكتلة", max_length=20, choices=Kind.choices)
    title = models.CharField("العنوان الظاهر", max_length=80, blank=True)
    layout = models.CharField("التخطيط", max_length=10, choices=Layout.choices, default=Layout.GRID)
    count = models.PositiveSmallIntegerField("عدد المواد", default=6)
    category = models.ForeignKey(
        "content.Category", verbose_name="القسم", null=True, blank=True, on_delete=models.CASCADE
    )
    categories = models.ManyToManyField(
        "content.Category", verbose_name="الأقسام (للأعمدة)", blank=True, related_name="+"
    )
    dossier = models.ForeignKey(
        "content.Dossier", verbose_name="الملف", null=True, blank=True, on_delete=models.CASCADE
    )
    article_kind = models.CharField("نوع المادة", max_length=20, blank=True)
    ad_slot = models.ForeignKey("core.AdSlot", verbose_name="الإعلان", null=True, blank=True, on_delete=models.SET_NULL)
    html = models.TextField("المحتوى الحر", blank=True)
    background = models.CharField("خلفية الكتلة", max_length=10, choices=Background.choices, blank=True, default="")
    subtitle = models.CharField("سطر تعريفي", max_length=200, blank=True)
    text = models.TextField("نص", blank=True)
    link = models.CharField("الرابط", max_length=300, blank=True)
    button_label = models.CharField("نص الزر", max_length=60, blank=True)
    image = models.ForeignKey(
        "content.MediaAsset", verbose_name="الصورة", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    items = models.TextField(
        "العناصر",
        blank=True,
        help_text="سطر لكل عنصر. للأرقام: القيمة | الوصف (مثل: 84493+ | خبر منشور). "
        "للمنصات: اسم المنصة | عدد المتابعين (مثل: telegram | 1.2 مليون).",
    )
    articles = models.ManyToManyField(
        "content.Article", verbose_name="المواد المختارة", blank=True, related_name="+", through="HomeBlockArticle"
    )
    order = models.PositiveSmallIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("مفعّلة", default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "كتلة رئيسية"
        verbose_name_plural = "كتل الصفحة الرئيسية"

    def __str__(self) -> str:
        return self.title or self.get_kind_display()

    @property
    def article_kind_label(self) -> str:
        from arcms.content.models import ArticleKind

        return dict(ArticleKind.choices).get(self.article_kind, self.article_kind)

    @property
    def dark(self) -> bool:
        return self.background == self.Background.DARK

    @property
    def section_class(self) -> str:
        return f"bg-{self.background}" if self.background else ""

    def item_pairs(self) -> list[tuple[str, str]]:
        """«القيمة | الوصف» لكل سطر غير فارغ."""
        pairs = []
        for line in (self.items or "").splitlines():
            if not line.strip():
                continue
            first, _, rest = line.partition("|")
            pairs.append((first.strip(), rest.strip()))
        return pairs

    def safe_link(self) -> str:
        from arcms.core.utils import is_safe_link

        return self.link if is_safe_link(self.link) else ""


class HomeBlockArticle(models.Model):
    """ترتيب المواد التي يختارها المحرر يدوياً لكتلة."""

    block = models.ForeignKey(HomeBlock, on_delete=models.CASCADE)
    article = models.ForeignKey("content.Article", on_delete=models.CASCADE, related_name="+")
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]
        unique_together = [("block", "article")]


class AdSlot(models.Model):
    class Placement(models.TextChoices):
        HEADER = "header", "أعلى الصفحة"
        SIDEBAR = "sidebar", "الشريط الجانبي"
        ARTICLE_INLINE = "article_inline", "داخل المقال"
        ARTICLE_END = "article_end", "نهاية المقال"
        HOME = "home", "بين كتل الرئيسية"

    name = models.CharField("الاسم", max_length=80)
    placement = models.CharField("المكان", max_length=20, choices=Placement.choices)
    image = models.ForeignKey(
        "content.MediaAsset", verbose_name="صورة الإعلان", null=True, blank=True, on_delete=models.SET_NULL
    )
    link = models.URLField("رابط الإعلان", blank=True)
    html = models.TextField("شيفرة الإعلان", blank=True, help_text="تُستخدم بدلاً من الصورة إن وُجدت.")
    starts_at = models.DateTimeField("يبدأ", null=True, blank=True)
    ends_at = models.DateTimeField("ينتهي", null=True, blank=True)
    is_active = models.BooleanField("مفعّل", default=True)

    class Meta:
        verbose_name = "مساحة إعلانية"
        verbose_name_plural = "المساحات الإعلانية"

    def __str__(self) -> str:
        return self.name

    @classmethod
    def live_for(cls, placement: str):
        now = timezone.now()
        return (
            cls.objects.filter(placement=placement, is_active=True)
            .filter(models.Q(starts_at__isnull=True) | models.Q(starts_at__lte=now))
            .filter(models.Q(ends_at__isnull=True) | models.Q(ends_at__gt=now))
            .select_related("image")
            .first()
        )


class Job(models.Model):
    """مهمة في الطابور الخلفي. قاعدة البيانات هي الطابور: لا Redis ولا Celery."""

    class Status(models.TextChoices):
        QUEUED = "queued", "في الانتظار"
        RUNNING = "running", "قيد التنفيذ"
        DONE = "done", "تمت"
        FAILED = "failed", "فشلت"

    kind = models.CharField(max_length=60, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED, db_index=True)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    dedupe_key = models.CharField(max_length=120, null=True, blank=True, unique=True)
    locked_by = models.CharField(max_length=80, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["run_after", "id"]
        indexes = [models.Index(fields=["status", "run_after"])]

    def __str__(self) -> str:
        return f"{self.kind}#{self.pk} ({self.status})"

    def backoff(self) -> timedelta:
        return timedelta(seconds=min(3600, 30 * (2 ** max(0, self.attempts - 1))))


class WorkerHeartbeat(models.Model):
    name = models.CharField(max_length=80, unique=True)
    beat_at = models.DateTimeField(default=timezone.now)
    info = models.JSONField(default=dict, blank=True)

    @classmethod
    def healthy(cls, within_seconds: int = 180) -> bool:
        cutoff = timezone.now() - timedelta(seconds=within_seconds)
        return cls.objects.filter(beat_at__gte=cutoff).exists()
