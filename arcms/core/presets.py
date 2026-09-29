"""قوالب جاهزة لبنية الموقع. «فلسطين» تحاكي بنية شبكات الأخبار الفلسطينية
(أقسام جغرافية، ترجمات عبرية، أسرى، تقارير، مقالات، فيديو، إنفوجراف)،
و«عام» لغرفة أخبار عربية عامة. كل شيء قابل للتعديل لاحقاً من اللوحة."""

PRESETS = {
    "palestine": {
        "label": "شبكة إخبارية فلسطينية",
        "categories": [
            ("القدس", "#b0101c"),
            ("الضفة الغربية", "#1c6b3a"),
            ("غزة", "#0f5e8c"),
            ("الأسرى", "#6b3a1c"),
            ("الداخل المحتل", "#5b2a9a"),
            ("شؤون إسرائيلية", "#3c4650"),
            ("عربي ودولي", "#8a5a00"),
            ("مجتمع وثقافة", "#a3346b"),
            ("اقتصاد", "#2e6f73"),
        ],
        "main_menu": [
            ("latest", "آخر الأخبار", None),
            ("category", "القدس", "القدس"),
            ("category", "الضفة", "الضفة الغربية"),
            ("category", "غزة", "غزة"),
            ("category", "الأسرى", "الأسرى"),
            ("kind", "تقارير", "report"),
            ("kind", "ترجمات عبرية", "translation"),
            ("kind", "مقالات", "opinion"),
            ("kind", "مدونات", "blog"),
            ("kind", "فيديو", "video"),
            ("kind", "بودكاست", "podcast"),
            ("kind", "إنفوجراف", "infographic"),
            ("live", "مباشر", None),
        ],
        # بترتيب واجهات شبكات الأخبار الفلسطينية الحديثة: واجهة وأبرز العناوين، موجز، أشرطة متحركة،
        # تقارير ومختارات بخلفية، ترويج للقناة، فيديو وقصير، أرقام، بودكاست، منصات، الأكثر قراءة، النشرة.
        "blocks": [
            ("hero", "أبرز العناوين", {"count": 6, "layout": "list"}),
            ("brief", "موجز الأخبار", {"count": 8, "subtitle": "استمع"}),
            ("live", "", {}),
            ("latest", "أخبار", {"count": 10, "layout": "carousel"}),
            ("columns", "", {"categories": ["القدس", "الضفة الغربية", "غزة"], "count": 4}),
            ("category", "متابعات القدس", {"category": "القدس", "layout": "overlay", "count": 8}),
            ("kind", "تقارير", {"article_kind": "report", "layout": "feature", "count": 5, "background": "muted"}),
            ("opinion", "مدونات", {"article_kind": "blog", "layout": "carousel", "count": 8}),
            ("kind", "ترجمات عبرية", {"article_kind": "translation", "layout": "carousel", "count": 8}),
            ("picks", "مختارات المحررين", {"layout": "feature", "count": 5, "background": "muted"}),
            ("promo", "تابعونا على قناتنا في تيليجرام", {
                "subtitle": "عاجل أولاً بأول", "text": "الخبر لحظة وقوعه، والصور والفيديو من الميدان، في قناة واحدة.",
                "button_label": "انضم إلى القناة", "background": "primary",
            }),
            ("video", "فيديو", {"count": 5, "background": "dark"}),
            ("video", "قصير", {"article_kind": "short", "layout": "reels", "count": 10, "background": "dark"}),
            ("stats", "حضورٌ يصنع الفارق في تغطية الحدث", {
                "subtitle": "بالأرقام", "background": "primary",
                "items": "84493+ | خبر منشور\n15+ | سنة من التغطية\n7+ | منصات رقمية\n10+ مليون | متابع",
            }),
            ("kind", "بودكاست", {"article_kind": "podcast", "layout": "carousel", "count": 8}),
            ("platforms", "تابعنا على", {"items": "telegram | \nwhatsapp | \nx | \nfacebook | \ninstagram | \nyoutube | "}),
            ("most_read", "الأكثر قراءة", {"count": 10}),
            ("poll", "رأيك", {"background": "muted"}),
            ("kind", "إنفوجراف", {"article_kind": "infographic", "layout": "strip", "count": 4}),
            ("newsletter", "ابقَ على اطلاع بأحدث الأخبار", {"subtitle": "النشرة اليومية", "background": "primary"}),
        ],
    },
    "general": {
        "label": "غرفة أخبار عربية عامة",
        "categories": [
            ("محليات", "#b0101c"),
            ("سياسة", "#3c4650"),
            ("عربي ودولي", "#8a5a00"),
            ("اقتصاد", "#2e6f73"),
            ("رياضة", "#1c6b3a"),
            ("ثقافة وفنون", "#a3346b"),
            ("تكنولوجيا", "#0f5e8c"),
            ("صحة", "#5b2a9a"),
        ],
        "main_menu": [
            ("latest", "آخر الأخبار", None),
            ("category", "محليات", "محليات"),
            ("category", "سياسة", "سياسة"),
            ("category", "عربي ودولي", "عربي ودولي"),
            ("category", "اقتصاد", "اقتصاد"),
            ("category", "رياضة", "رياضة"),
            ("category", "ثقافة وفنون", "ثقافة وفنون"),
            ("kind", "تقارير", "report"),
            ("kind", "آراء", "opinion"),
            ("kind", "فيديو", "video"),
        ],
        "blocks": [
            ("hero", "أبرز العناوين", {"count": 6, "layout": "list"}),
            ("brief", "موجز الأخبار", {"count": 8, "subtitle": "استمع"}),
            ("live", "", {}),
            ("latest", "آخر الأخبار", {"count": 10, "layout": "carousel"}),
            ("columns", "", {"categories": ["محليات", "سياسة", "عربي ودولي"], "count": 4}),
            ("category", "", {"category": "اقتصاد", "layout": "feature", "count": 5, "background": "muted"}),
            ("opinion", "آراء", {"count": 8, "layout": "carousel"}),
            ("picks", "مختارات المحررين", {"layout": "feature", "count": 5, "background": "muted"}),
            ("promo", "تابعونا على قناتنا في تيليجرام", {
                "subtitle": "الأخبار أولاً بأول", "text": "أهم الأخبار لحظة وقوعها في قناة واحدة.",
                "button_label": "انضم إلى القناة", "background": "primary",
            }),
            ("video", "فيديو", {"count": 5, "background": "dark"}),
            ("category", "", {"category": "رياضة", "layout": "overlay", "count": 8}),
            ("category", "", {"category": "ثقافة وفنون", "layout": "carousel", "count": 8}),
            ("kind", "بودكاست", {"article_kind": "podcast", "layout": "carousel", "count": 8}),
            ("platforms", "تابعنا على", {}),
            ("most_read", "الأكثر قراءة", {"count": 10}),
            ("poll", "رأيك", {"background": "muted"}),
            ("newsletter", "النشرة اليومية", {"subtitle": "كل صباح", "background": "primary"}),
        ],
    },
}

PAGES = [
    ("من نحن", "about", False, "<p>مؤسسة إعلامية مستقلة تنقل الخبر من الميدان بدقة ومسؤولية. نلتزم بالتحقق من كل معلومة قبل نشرها، وبتصحيح أي خطأ علناً.</p><h2>سياستنا التحريرية</h2><p>نفصل بين الخبر والرأي، وننسب كل معلومة إلى مصدرها، ونحمي مصادرنا.</p>"),
    ("اتصل بنا", "contact", True, "<p>لإرسال ملاحظة أو تصحيح أو معلومة، استخدم النموذج أدناه. للمعلومات الحساسة استخدم قنوات مشفّرة (سيغنال أو تيليجرام بمحادثة سرية).</p>"),
    ("سياسة الخصوصية", "privacy", False, "<p>لا نستخدم ملفات تعريف ارتباط للتتبع، ولا نحمّل أي سكربت من شركات إعلانية أو تحليلية. نحسب عدد القرّاء على خوادمنا فقط، ولا نخزّن عناوين IP؛ يُمثَّل الزائر ببصمة يومية تُمحى مفاتيحها كل ليلة.</p><p>بريدك في النشرة يُستخدم لإرسالها فقط، ويمكنك إلغاء الاشتراك بنقرة من أي رسالة.</p>"),
    ("شروط الاستخدام", "terms", False, "<p>يجوز الاقتباس من موادنا مع ذكر المصدر ورابط المادة.</p>"),
    ("أعلن معنا", "advertise", True, "<p>للإعلان على الموقع، راسلنا عبر النموذج أدناه.</p>"),
]


# مظاهر جاهزة: تغيّر الألوان والخطوط وشكل الترويسة والزوايا فقط، لا الهوية ولا المحتوى.
THEMES = {
    "modern-blue": {
        "label": "أزرق حديث",
        "primary_color": "#1463d8", "accent_color": "#0b1f3a", "header_dark": False,
        "header_style": "compact", "corner_style": "round", "font_headings": "plex", "font_body": "plex",
    },
    "classic-red": {
        "label": "أحمر كلاسيكي",
        "primary_color": "#b0101c", "accent_color": "#111418", "header_dark": False,
        "header_style": "classic", "corner_style": "soft", "font_headings": "plex", "font_body": "naskh",
    },
    "olive-green": {
        "label": "أخضر زيتوني",
        "primary_color": "#1c6b3a", "accent_color": "#10231a", "header_dark": False,
        "header_style": "compact", "corner_style": "soft", "font_headings": "kufi", "font_body": "naskh",
    },
    "night": {
        "label": "ليلي",
        "primary_color": "#d4a017", "accent_color": "#0f1113", "header_dark": True,
        "header_style": "classic", "corner_style": "sharp", "font_headings": "kufi", "font_body": "plex",
    },
    "sky-cairo": {
        "label": "سماوي بخط القاهرة",
        "primary_color": "#0077b6", "accent_color": "#03263b", "header_dark": False,
        "header_style": "compact", "corner_style": "round", "font_headings": "cairo", "font_body": "tajawal",
    },
    "maroon": {
        "label": "عنّابي رصين",
        "primary_color": "#7a1f2b", "accent_color": "#1d1a1b", "header_dark": False,
        "header_style": "compact", "corner_style": "sharp", "font_headings": "naskh", "font_body": "naskh",
    },
}


def apply_theme(site, key: str) -> bool:
    theme = THEMES.get(key)
    if not theme:
        return False
    for field, value in theme.items():
        if field != "label":
            setattr(site, field, value)
    for field in site.COLOR_AREAS:  # ألوان المناطق تعود تلقائية فيبقى المظهر متسقاً
        setattr(site, field, "")
    site.save()
    return True


def create_blocks(blocks, cats) -> None:
    """ينشئ كتل الصفحة الرئيسية من قائمة (النوع، العنوان، الإعدادات)؛ cats: اسم القسم ← القسم."""
    from arcms.core.models import HomeBlock

    for order, (kind, title, cfg) in enumerate(blocks):
        block = HomeBlock.objects.create(
            kind=kind,
            title=title,
            order=(order + 1) * 10,
            count=cfg.get("count", 6),
            layout=cfg.get("layout", HomeBlock.Layout.GRID),
            article_kind=cfg.get("article_kind", ""),
            category=cats.get(cfg.get("category")),
            background=cfg.get("background", ""),
            subtitle=cfg.get("subtitle", ""),
            text=cfg.get("text", ""),
            link=cfg.get("link", ""),
            button_label=cfg.get("button_label", ""),
            items=cfg.get("items", ""),
        )
        if cfg.get("categories"):
            block.categories.set([cats[c] for c in cfg["categories"]])
