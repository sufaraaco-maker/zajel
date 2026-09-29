"""موقع تجريبي كامل للتجربة المحلية: مستخدمون بكل الرتب، مواد بكل الأنواع، صور حقيقية بتراخيص حرة
(أو مولَّدة دون اتصال)، عاجل، تغطية مباشرة، ملف خاص، وأرقام جمهور لثلاثين يوماً.
ثلاث هويات جاهزة (--brand): زاجل وسنبلة وأفق، لكل منها شعار وألوان وخطوط وواجهة.
لا يُستخدم على خادم إنتاج."""

from __future__ import annotations

import hashlib
import io
import math
import random
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from PIL import Image, ImageDraw, ImageFilter

from arcms.accounts import totp
from arcms.accounts.models import User
from arcms.accounts.roles import Role
from arcms.audit.services import acting_as, suppressed
from arcms.content.imaging import ImageRejected, store_image
from arcms.content.models import (
    Article,
    Author,
    BreakingNews,
    Category,
    Dossier,
    GalleryItem,
    LiveCoverage,
    LiveEntry,
    Status,
    Tag,
)
from arcms.core.demo_brands import BRANDS
from arcms.core.demo_data import (
    ARTICLES,
    AUTHORS,
    BREAKING,
    EXTRA_ARTICLES,
    LIVE,
    WIRE_ITEMS,
    WIRE_KEYWORDS,
    silent_mp3,
)
from arcms.core.demo_photos import CommonsPhotos, credits_html, query_for
from arcms.core.models import HomeBlock, MenuItem, SiteSettings

LOGOS = Path(__file__).resolve().parents[2] / "demo_assets" / "logos"

DEMO_PASSWORD = "zajel-demo-2026"
USERS = [
    ("admin", "مدير النظام", Role.ADMIN),
    ("chief", "رئيس التحرير", Role.CHIEF),
    ("deskhead", "رئيسة القسم", Role.DESK_HEAD),
    ("editor", "المحرر المناوب", Role.EDITOR),
    ("reporter", "سلمى الخطيب", Role.REPORTER),
    ("social", "محرر المنصات", Role.SOCIAL),
]


def _hex(c: str) -> tuple[int, int, int]:
    c = c.lstrip("#")
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def make_art(seed: int, color: str, tall: bool = False) -> bytes:
    """لوحة تجريدية بدل الصور الحقيقية: تدرّج ودوائر وخطوط بلون القسم."""
    rnd = random.Random(seed)
    w, h = (900, 1200) if tall else (1600, 900)
    base = _hex(color or "#3c4650")
    dark = tuple(max(0, int(v * 0.35)) for v in base)
    light = tuple(min(255, int(v + (255 - v) * 0.55)) for v in base)
    img = Image.new("RGB", (w, h), dark)
    grad = Image.linear_gradient("L").resize((w, h)).rotate(rnd.choice([0, 30, 60, 120, 150]), expand=False)
    img = Image.composite(Image.new("RGB", (w, h), base), img, grad)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for _ in range(rnd.randint(5, 9)):
        r = rnd.randint(int(h * 0.12), int(h * 0.55))
        x, y = rnd.randint(-r // 2, w), rnd.randint(-r // 2, h)
        tone = rnd.choice([light, base, (255, 255, 255), dark])
        d.ellipse([x - r, y - r, x + r, y + r], fill=(*tone, rnd.randint(28, 90)))
    for i in range(rnd.randint(8, 16)):
        y = int(h * (0.55 + 0.45 * math.sin(i + seed)))
        d.line([(0, y), (w, y - rnd.randint(-120, 120))], fill=(255, 255, 255, 30), width=rnd.randint(1, 3))
    img = Image.alpha_composite(img.convert("RGBA"), layer.filter(ImageFilter.GaussianBlur(2))).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def with_fake_gps(jpeg: bytes) -> bytes:
    """صورة «من هاتف مراسل» تحمل إحداثيات وطراز جهاز، لتجربة النزع."""
    img = Image.open(io.BytesIO(jpeg))
    exif = img.getexif()
    exif[0x010F] = "PhoneMaker"
    exif[0x0110] = "Model X"
    exif[0x013B] = "Reporter Name"
    gps = exif.get_ifd(0x8825)
    gps.update({1: "N", 2: (31.0, 46.0, 30.0), 3: "E", 4: (35.0, 13.0, 45.0)})
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif, quality=90)
    return buf.getvalue()


DEMO_STYLE_RULES = """# دليل أسلوب تجريبي: عدّله من «دليل الأسلوب» في غرفة التحرير
مسئول* => مسؤول | نكتب الهمزة على الواو
تم | «تمّ» مع المصدر أضعف من الفعل: «اعتُقل» لا «تمّ اعتقاله»
قام ب* | «قام بـ» زائدة غالباً: «زار» لا «قام بزيارة»
أكد على => أكد | «أكّد» يتعدى بنفسه
"""


class Command(BaseCommand):
    help = "ينشئ موقعاً تجريبياً كاملاً للتجربة المحلية (لا تشغّله على خادم الإنتاج)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="التشغيل حتى لو وُجدت مواد")
        parser.add_argument("--no-traffic", action="store_true")
        parser.add_argument("--brand", choices=list(BRANDS), default="zajel",
                            help="الهوية: " + "، ".join(f"{k} ({b['name']})" for k, b in BRANDS.items()))
        parser.add_argument("--theme", help="مظهر جاهز بدل ألوان الهوية وخطوطها")
        parser.add_argument("--no-photos", action="store_true", help="صور مولَّدة بدل صور ويكيميديا كومنز الحقيقية")
        parser.add_argument("--photos-cache", help="مجلد حفظ الصور المنزّلة (الافتراضي var/demo-photos)")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("هذا الأمر للتجربة المحلية فقط. استخدم --force إن كنت متأكداً.")
        if Article.objects.exists() and not opts["force"]:
            raise CommandError("توجد مواد بالفعل. استخدم --force لإضافة المحتوى التجريبي.")
        self.brand = brand = BRANDS[opts["brand"]]
        self.photos = None
        if not opts["no_photos"]:
            cache = Path(opts["photos_cache"]) if opts.get("photos_cache") else settings.BASE_DIR / "var" / "demo-photos"
            self.photos = CommonsPhotos(cache, log=lambda msg: self.stdout.write(self.style.WARNING(msg)))
            self.stdout.write("تنزيل صور حقيقية بتراخيص حرة من ويكيميديا كومنز (تُحفظ للمرات القادمة)…")
        call_command("arcms_setup", preset=brand["preset"], site_name=brand["name"], short_name=brand["short_name"],
                     verbosity=0, stdout=self.stdout)
        with acting_as(label="المحتوى التجريبي"), suppressed(), transaction.atomic():
            site = SiteSettings.objects.get(pk=1)
            site.tagline = brand["tagline"]
            site.description = f"{brand['name']}: موقع تجريبي لمنصة arcms — المحتوى متخيَّل."
            site.telegram = "https://t.me/example"
            site.x_twitter = "https://x.com/example"
            site.facebook = "https://facebook.com/example"
            site.youtube = "https://youtube.com/@example"
            site.instagram = "https://instagram.com/example"
            site.whatsapp = "https://whatsapp.com/channel/example"
            site.alt_language_url = "https://example.org/en"
            site.footer_about = brand["footer_about"]
            site.header_cta_label = "أرسل معلومة"
            site.header_cta_url = "/tips/"
            site.app_android_url = "https://play.google.com/store/apps/details?id=org.example.news"
            site.app_ios_url = "https://apps.apple.com/app/id0000000000"
            site.style_rules = DEMO_STYLE_RULES
            site.save()
            from arcms.core.presets import apply_theme, create_blocks

            apply_theme(site, opts.get("theme") or brand["theme"])
            if not opts.get("theme"):
                for field, value in brand["settings"].items():
                    setattr(site, field, value)
            self._logos(site, brand)
            site.save()
            if brand.get("blocks"):
                HomeBlock.objects.all().delete()
                create_blocks(brand["blocks"], {c.name: c for c in Category.objects.all()})
            creds = self._users()
            authors = self._authors(site)
            self._articles(authors)
            self._extras()
            self._credits_page()
        if not opts["no_traffic"]:
            self._traffic()
        call_command("arcms_reindex", verbosity=0, stdout=self.stdout)
        if not opts["no_traffic"]:
            self._searches()
        lines = [f"{u}\t{DEMO_PASSWORD}\tTOTP: {s}" for u, s in creds]
        self.stdout.write(self.style.SUCCESS(f"اكتمل الموقع التجريبي: {brand['name']}."))
        if self.photos is not None:
            n = len(self.photos.credits)
            self.stdout.write(f"صور حقيقية: {n}" + ("" if n else " (تعذّر التنزيل؛ استُخدمت صور مولَّدة)"))
        path = settings.BASE_DIR / "var" / "demo-credentials.txt"
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.stdout.write(f"بيانات الدخول وأسرار التحقق الثنائي (للتجربة فقط): {path}")
        except OSError:
            self.stdout.write("بيانات الدخول وأسرار التحقق الثنائي (للتجربة فقط):")
        for line in lines:
            self.stdout.write("  " + line)

    def _logos(self, site, brand):
        for field, suffix in (("logo", ""), ("logo_dark", "-dark")):
            path = LOGOS / f"{brand['logo']}{suffix}.png"
            if path.exists():
                asset, _ = store_image(path.read_bytes(), title=f"شعار {brand['name']}", alt=brand["name"])
                setattr(site, field, asset)

    def _image(self, data: dict, seed: int, color: str, *, caption: str, gps: bool = False):
        """صورة حقيقية مناسبة للمادة إن أمكن، وإلا لوحة مولَّدة بلون القسم."""
        tall = data.get("tall", False)
        photo = self.photos.get(query_for(data, data.get("source_category", "")), tall=tall) if self.photos else None
        if photo:
            try:
                image, _ = store_image(with_fake_gps(photo.data) if gps else photo.data, caption=caption, credit=photo.credit)
                return image
            except ImageRejected:
                pass
        raw = make_art(seed, color, tall=tall)
        image, _ = store_image(with_fake_gps(raw) if gps else raw, caption=caption,
                               credit=f"{self.brand['short_name']} / صورة توضيحية")
        return image

    def _users(self):
        creds = []
        for username, name, role in USERS:
            if username == "deskhead":
                name = self.brand["desk_label"]
            user, created = User.objects.get_or_create(username=username, defaults={"display_name": name, "role": role})
            if created:
                user.set_password(DEMO_PASSWORD)
                user.email = f"{username}@example.org"
                user.totp_secret = totp.generate_secret()
                user.totp_confirmed_at = timezone.now()
                codes = totp.generate_recovery_codes()
                user.recovery_codes = [totp.hash_recovery_code(c) for c in codes]
                user.save()
            creds.append((username, user.totp_secret))
        desk = Category.objects.filter(name=self.brand["desk"]).first()
        if desk:
            desk.desk_members.add(User.objects.get(username="deskhead"))
        return creds

    def _authors(self, site):
        authors = {}
        reporter = User.objects.get(username="reporter")
        for n, (name, title, columnist) in enumerate(AUTHORS):
            tones = ["#8a5a00", "#0f5e8c", "#a3346b", "#1c6b3a", "#5b2a9a", "#2e6f73", "#b0101c", "#3c4650"]
            photo, _ = store_image(make_art(900 + n, tones[n % len(tones)]), caption=name)
            author, _ = Author.objects.get_or_create(
                name=name,
                defaults={"title": title, "is_columnist": columnist, "photo": photo, "bio": f"{title} في {site.name}."},
            )
            authors[name] = author
        authors["سلمى الخطيب"].user = reporter
        authors["سلمى الخطيب"].save()
        return authors

    def _article_list(self) -> list[dict]:
        """مواد الهوية أولاً (أحدث وأبرز)، ثم المحتوى المشترك بعد مواءمة أقسامه مع أقسام القالب."""
        brand = self.brand
        items = [dict(a) for a in brand["articles"]]
        for data in [*ARTICLES, *EXTRA_ARTICLES]:
            if data["category"] in brand["skip"]:
                continue
            item = dict(data, source_category=data["category"])
            item["category"] = brand["category_map"].get(data["category"], data["category"])
            if brand["articles"]:  # واجهة الهوية لموادها الخاصة
                item["featured"], item["priority"] = False, 0
            items.append(item)
        return items

    def _articles(self, authors):
        now = timezone.now()
        chief = User.objects.get(username="chief")
        reporter = User.objects.get(username="reporter")
        cats = {c.name: c for c in Category.objects.all()}
        for i, data in enumerate(self._article_list()):
            cat = cats.get(data["category"])
            image = self._image(data, i + 1, cat.color if cat else "#3c4650", gps=i == 0,
                                caption=data.get("subtitle", "")[:120] or data["title"])
            when = now - timedelta(minutes=35 + i * 97 + random.Random(i).randint(0, 60))
            article = Article.objects.create(
                kind=data["kind"],
                title=data["title"],
                subtitle=data.get("subtitle", ""),
                body=data["body"],
                dateline=data.get("dateline", ""),
                category=cat,
                featured_image=image,
                video_url=data.get("video_url", ""),
                is_featured=data.get("featured", False),
                is_exclusive=data.get("is_exclusive", False),
                priority=data.get("priority", 0),
                status=Status.PUBLISHED,
                published_at=when,
                first_published_at=when,
                content_updated_at=when,
                distributed_at=when,
                created_at=when - timedelta(hours=1),
                created_by=reporter if data["author"] == "سلمى الخطيب" else chief,
                published_by=chief,
                reviewed_by=chief,
                source_notes="مصدر أول: موظف في البلدية (لا يُذكر اسمه). تم التحقق عبر اتصال هاتفي." if i == 0 else "",
                correction=data.get("correction", ""),
                corrected_at=when + timedelta(minutes=50) if data.get("correction") else None,
            )
            if data.get("audio"):
                from django.core.files.base import ContentFile

                from arcms.content.audio import clean_audio

                clean = clean_audio(silent_mp3())
                article.audio.save(clean.filename, ContentFile(clean.content), save=True)
            article.authors.set([authors[data["author"]]])
            article.tags.set([Tag.get_or_create_by_name(t) for t in data.get("tags", [])])
            for n in range(data.get("gallery", 0)):
                g = self._image(data, 500 + i * 10 + n, cat.color if cat else "#555", caption=f"صورة {n + 1}")
                GalleryItem.objects.create(article=article, media=g, order=n, caption=f"من السوق صباحاً ({n + 1})")
        # مواد في مراحل التحرير لتجربة سير العمل
        for title, status in (
            ("مسودة: تقرير عن أسعار الإيجارات في المدن", Status.DRAFT),
            ("بانتظار المراجعة: افتتاح ملعب بلدي جديد في طولكرم", Status.IN_REVIEW),
            ("أُعيدت للتعديل: جولة في معرض الصناعات المحلية", Status.CHANGES),
        ):
            Article.objects.create(
                title=title, kind="news", status=status, category=cats.get(self.brand["desk"]), created_by=reporter,
                body="<p>نص أولي للمادة يحتاج إلى استكمال.</p>", submitted_at=now if status != Status.DRAFT else None,
            )

    def _extras(self):
        now = timezone.now()
        chief = User.objects.get(username="chief")
        brand = self.brand
        for n, text in enumerate(brand.get("breaking", BREAKING)):
            BreakingNews.objects.create(text=text, created_by=chief, created_at=now - timedelta(minutes=20 + n * 50),
                                        send_telegram=False, send_push=False)
        live_data = brand.get("live", LIVE)
        live = LiveCoverage.objects.create(title=live_data["title"], summary=live_data["summary"], created_by=chief,
                                           started_at=now - timedelta(hours=3))
        for n, text in enumerate(live_data["entries"]):
            LiveEntry.objects.create(coverage=live, body=text, author=chief, created_at=now - timedelta(minutes=170 - n * 40),
                                     is_important=n == 1)
        spec = brand["dossier"]
        cover = self._image({"photo": spec["photo"]}, 777, spec["color"], caption=spec["title"])
        dossier = Dossier.objects.create(title=spec["title"], cover=cover, color=spec["color"], description=spec["description"])
        dossier.articles.set(Article.objects.filter(tags__name__in=spec["tags"]).distinct())
        HomeBlock.objects.create(kind=HomeBlock.Kind.DOSSIER, dossier=dossier, order=45, count=4)
        # كتل الواجهة التي تحتاج بيانات الموقع التجريبي: رابط القناة للترويج، ومختارات المحررين، وأعداد المتابعين
        site = SiteSettings.objects.get(pk=1)
        HomeBlock.objects.filter(kind=HomeBlock.Kind.PROMO, link="").update(
            link=getattr(site, brand.get("promo_channel", "telegram")) or site.telegram
        )
        HomeBlock.objects.filter(kind=HomeBlock.Kind.PLATFORMS).update(items=brand["platforms"])
        from arcms.core.models import HomeBlockArticle

        picks = HomeBlock.objects.filter(kind=HomeBlock.Kind.PICKS).first()
        if picks:
            chosen = Article.objects.published().filter(kind__in=["investigation", "interview", "report", "gallery"])[:5]
            HomeBlockArticle.objects.bulk_create(
                [HomeBlockArticle(block=picks, article=a, order=n) for n, a in enumerate(chosen)]
            )
        self._wires(site)
        self._planning()
        self._poll()
        from arcms.tips.services import add_newsroom_reply, create_tip

        tip, _ = create_tip(
            subject="حفريات قرب السور ليلاً",
            body="منذ ثلاث ليالٍ تعمل آليات بعد منتصف الليل قرب الجهة الجنوبية من السور. صوّرت من نافذة البيت.",
            files=[with_fake_gps(make_art(4242, "#3b3f46"))],
        )
        add_newsroom_reply(tip, chief, "شكراً لك. هل تعرف الجهة المنفّذة؟ ولا ترسل صوراً من النافذة نفسها مرة أخرى.")

    def _wires(self, site):
        """مكتب وكالات تجريبي: مصدر معطّل (لا جلب من الشبكة) بمواد وكلمات تنبيه."""
        from arcms.arabic.normalize import normalize
        from arcms.wires.models import WireItem, WireKeyword, WireSource

        now = timezone.now()
        source = WireSource.objects.create(
            name="وكالة الأنباء (تجريبية)", feed_url="https://example.org/agency/rss.xml", credit="وكالة الأنباء",
            category=Category.objects.filter(name=self.brand["desk"]).first(), is_active=False,
        )
        for word in WIRE_KEYWORDS:
            WireKeyword.objects.get_or_create(word=word)
        words = [normalize(w) for w in WIRE_KEYWORDS]
        for n, (title, summary) in enumerate(WIRE_ITEMS):
            blob = normalize(f"{title} {summary}")
            WireItem.objects.create(
                source=source, guid_hash=hashlib.sha256(f"demo-{n}".encode()).hexdigest(), title=title, summary=summary,
                link=f"https://example.org/agency/{n}", published_at=now - timedelta(minutes=4 + n * 23),
                fetched_at=now - timedelta(minutes=3 + n * 23), search_text=blob, is_alert=any(w in blob for w in words),
            )

    def _planning(self):
        """خطة تغطية تجريبية بمراحل مختلفة، بعضها متأخر."""
        from arcms.planning.models import Assignment

        now = timezone.now()
        desk = User.objects.get(username="deskhead")
        reporter = User.objects.get(username="reporter")
        editor = User.objects.get(username="editor")
        cat = Category.objects.filter(name=self.brand["desk"]).first()
        rows = [
            ("مؤتمر صحفي لوزارة الصحة عن موسم الإنفلونزا", reporter, now + timedelta(hours=3), 1, "اسأل عن توفر اللقاحات في المراكز الريفية."),
            ("جولة في سوق الخضار المركزي صباح الجمعة", reporter, now + timedelta(days=1), 0, "صور وفيديو قصير للمنصات."),
            ("متابعة: نتائج امتحانات الثانوية العامة", editor, now - timedelta(hours=2), 2, "جهّز الرسوم البيانية مسبقاً."),
            ("حوار مع مدير المكتبة العامة الجديدة", None, None, 0, "فكرة للأسبوع القادم."),
            ("تقرير عن أزمة المواصلات صباحاً", editor, now + timedelta(hours=8), 1, ""),
        ]
        for title, who, due, priority, brief in rows:
            Assignment.objects.create(
                title=title, assignee=who, due_at=due, priority=priority, brief=brief, category=cat, created_by=desk,
                status=Assignment.Status.ASSIGNED if who else Assignment.Status.IDEA,
            )
        draft = Article.objects.filter(status=Status.DRAFT).first()
        if draft:
            Assignment.objects.create(title=draft.title, assignee=draft.created_by, article=draft, category=draft.category,
                                      created_by=desk, status=Assignment.Status.WORKING, due_at=now + timedelta(hours=5))
        published = Article.objects.published().filter(created_by=reporter).first()
        if published:
            Assignment.objects.create(title=published.title, assignee=reporter, article=published, created_by=desk,
                                      category=published.category, status=Assignment.Status.DONE)

    def _poll(self):
        """استطلاع مفتوح بأصوات تجريبية، يظهر في كتلة «رأيك»."""
        from arcms.polls.models import Poll, PollOption

        question, options = self.brand["poll"]
        poll = Poll.objects.create(question=question, created_by=User.objects.get(username="chief"))
        rnd = random.Random(7)
        total = 0
        for n, text in enumerate(options):
            votes = rnd.randint(40, 900)
            total += votes
            PollOption.objects.create(poll=poll, text=text, order=n, votes=votes)
        Poll.objects.filter(pk=poll.pk).update(total_votes=total)

    def _credits_page(self):
        """صفحة «مصادر الصور» في التذييل: نسبة كل صورة حقيقية لصاحبها وترخيصها."""
        if not self.photos or not self.photos.credits:
            return
        from arcms.content.models import Page
        from arcms.content.sanitize import sanitize_html

        page, _ = Page.objects.update_or_create(
            slug="photo-credits",
            defaults={"title": "مصادر الصور", "body": sanitize_html(credits_html(self.photos.credits)), "is_published": True},
        )
        MenuItem.objects.get_or_create(
            location=MenuItem.Location.FOOTER, page=page,
            defaults={"label": page.title, "link_type": MenuItem.LinkType.PAGE, "order": 20},
        )

    def _traffic(self):
        """زيارات مصطنعة لثلاثين يوماً حتى تمتلئ لوحة الجمهور و«الأكثر قراءة»."""
        from arcms.analytics.models import PageView
        from arcms.analytics.tasks import aggregate_day

        rnd = random.Random(42)
        articles = list(Article.objects.published().values_list("pk", "category_id", "published_at"))
        sources = ["direct"] * 5 + ["search"] * 4 + ["social"] * 5 + ["messaging"] * 6 + ["internal"] * 3 + ["push", "newsletter"]
        devices = ["mobile"] * 7 + ["desktop"] * 3 + ["tablet"]
        refs = {"search": "google.com", "social": "facebook.com", "messaging": "t.me"}
        now = timezone.now()
        views = []
        for day in range(30, -1, -1):
            base = 900 + 25 * (30 - day) + rnd.randint(-150, 250)
            if day % 7 in (5, 6):
                base = int(base * 0.8)
            if day == 0:
                base = int(base * min(1.0, timezone.localtime().hour / 24 + 0.15))
            for _ in range(base):
                aid, cat, pub = rnd.choice(articles + [(None, None, None)] * 3)
                ts = now - timedelta(days=day, seconds=rnd.randint(0, 86399 if day else 3600 * 3))
                src = rnd.choice(sources)
                views.append(PageView(
                    ts=ts, path="/" if aid is None else f"/post/{aid}/", article_id=aid, category_id=cat,
                    visitor=hashlib.sha256(f"{day}-{rnd.randint(0, base // 2)}".encode()).hexdigest()[:16],
                    source=src, referrer_host=refs.get(src, ""), device=rnd.choice(devices),
                ))
        PageView.objects.bulk_create(views, batch_size=2000)
        for day in range(30, 0, -1):
            aggregate_day(timezone.localdate() - timedelta(days=day))
        aggregate_day(timezone.localdate())
        from django.db.models import Count

        for row in PageView.objects.exclude(article_id__isnull=True).values("article_id").annotate(n=Count("id")):
            Article.objects.filter(pk=row["article_id"]).update(view_count=row["n"])

    def _searches(self):
        """ما بحث عنه القرّاء (مجمّعاً)، بعضه بلا نتائج ليظهر في «بحثوا ولم يجدوا». بعد الفهرسة."""
        from arcms.analytics.models import SearchStat
        from arcms.analytics.searches import key
        from arcms.content.search import search_articles

        rnd = random.Random(11)
        today = timezone.localdate()
        for day in range(7):
            for q, n in (("الزيتون", 40), ("امتحانات الثانوية", 55), ("أسعار الخضار", 22), ("المكتبة العامة", 18),
                         ("مواعيد الحافلات", 16), ("نتائج القبول الموحد", 12), ("الطقس", 30), ("دوري كرة السلة", 9)):
                SearchStat.objects.create(day=today - timedelta(days=day), query=key(q), sample=q,
                                          searches=max(1, n - day * 2 + rnd.randint(0, 6)),
                                          results=search_articles(q, limit=1).total)
