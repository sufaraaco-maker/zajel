"""موقع تجريبي كامل للتجربة المحلية: مستخدمون بكل الرتب، مواد بكل الأنواع، صور مولَّدة،
عاجل، تغطية مباشرة، ملف خاص، وأرقام جمهور لثلاثين يوماً. لا يُستخدم على خادم إنتاج."""

from __future__ import annotations

import hashlib
import io
import math
import random
from datetime import timedelta

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
from arcms.content.imaging import store_image
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
from arcms.core.demo_data import ARTICLES, AUTHORS, BREAKING, EXTRA_ARTICLES, LIVE, silent_mp3
from arcms.core.models import HomeBlock, SiteSettings

DEMO_PASSWORD = "zajel-demo-2026"
USERS = [
    ("admin", "مدير النظام", Role.ADMIN),
    ("chief", "رئيس التحرير", Role.CHIEF),
    ("deskhead", "رئيسة قسم الضفة", Role.DESK_HEAD),
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


class Command(BaseCommand):
    help = "ينشئ موقعاً تجريبياً كاملاً للتجربة المحلية (لا تشغّله على خادم الإنتاج)."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true", help="التشغيل حتى لو وُجدت مواد")
        parser.add_argument("--no-traffic", action="store_true")
        parser.add_argument("--theme", default="modern-blue", help="المظهر الجاهز للموقع التجريبي")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["force"]:
            raise CommandError("هذا الأمر للتجربة المحلية فقط. استخدم --force إن كنت متأكداً.")
        if Article.objects.exists() and not opts["force"]:
            raise CommandError("توجد مواد بالفعل. استخدم --force لإضافة المحتوى التجريبي.")
        call_command("arcms_setup", preset="palestine", site_name="زاجل الإخبارية", short_name="زاجل", verbosity=0)
        with acting_as(label="المحتوى التجريبي"), suppressed(), transaction.atomic():
            site = SiteSettings.objects.get(pk=1)
            site.tagline = "نسخة تجريبية من منصة arcms — محتوى متخيَّل"
            site.telegram = "https://t.me/example"
            site.x_twitter = "https://x.com/example"
            site.facebook = "https://facebook.com/example"
            site.youtube = "https://youtube.com/@example"
            site.instagram = "https://instagram.com/example"
            site.whatsapp = "https://whatsapp.com/channel/example"
            site.alt_language_url = "https://example.org/en"
            site.footer_about = "زاجل موقع تجريبي لمنصة arcms: منصة نشر إخبارية عربية تعمل على خوادم المؤسسة وتملك بياناتها."
            site.header_cta_label = "أرسل معلومة"
            site.header_cta_url = "/tips/"
            site.app_android_url = "https://play.google.com/store/apps/details?id=org.example.news"
            site.app_ios_url = "https://apps.apple.com/app/id0000000000"
            site.save()
            from arcms.core.presets import apply_theme

            apply_theme(site, opts.get("theme") or "modern-blue")
            creds = self._users()
            authors = self._authors()
            self._articles(authors)
            self._extras()
        if not opts["no_traffic"]:
            self._traffic()
        call_command("arcms_reindex", verbosity=0)
        lines = [f"{u}\t{DEMO_PASSWORD}\tTOTP: {s}" for u, s in creds]
        self.stdout.write(self.style.SUCCESS("اكتمل الموقع التجريبي."))
        path = settings.BASE_DIR / "var" / "demo-credentials.txt"
        try:
            path.parent.mkdir(exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.stdout.write(f"بيانات الدخول وأسرار التحقق الثنائي (للتجربة فقط): {path}")
        except OSError:
            self.stdout.write("بيانات الدخول وأسرار التحقق الثنائي (للتجربة فقط):")
        for line in lines:
            self.stdout.write("  " + line)

    def _users(self):
        creds = []
        for username, name, role in USERS:
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
        west_bank = Category.objects.filter(name="الضفة الغربية").first()
        if west_bank:
            west_bank.desk_members.add(User.objects.get(username="deskhead"))
        return creds

    def _authors(self):
        authors = {}
        reporter = User.objects.get(username="reporter")
        for n, (name, title, columnist) in enumerate(AUTHORS):
            tones = ["#8a5a00", "#0f5e8c", "#a3346b", "#1c6b3a", "#5b2a9a", "#2e6f73", "#b0101c", "#3c4650"]
            photo, _ = store_image(make_art(900 + n, tones[n % len(tones)]), caption=name)
            author, _ = Author.objects.get_or_create(
                name=name,
                defaults={"title": title, "is_columnist": columnist, "photo": photo, "bio": f"{title} في زاجل الإخبارية."},
            )
            authors[name] = author
        authors["سلمى الخطيب"].user = reporter
        authors["سلمى الخطيب"].save()
        return authors

    def _articles(self, authors):
        now = timezone.now()
        chief = User.objects.get(username="chief")
        reporter = User.objects.get(username="reporter")
        cats = {c.name: c for c in Category.objects.all()}
        for i, data in enumerate([*ARTICLES, *EXTRA_ARTICLES]):
            cat = cats.get(data["category"])
            raw = make_art(i + 1, cat.color if cat else "#3c4650", tall=data.get("tall", False))
            if i == 0:
                raw = with_fake_gps(raw)
            image, _ = store_image(raw, caption=data.get("subtitle", "")[:120] or data["title"], credit="زاجل / صورة توضيحية")
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
            )
            if data.get("audio"):
                from django.core.files.base import ContentFile

                from arcms.content.audio import clean_audio

                clean = clean_audio(silent_mp3())
                article.audio.save(clean.filename, ContentFile(clean.content), save=True)
            article.authors.set([authors[data["author"]]])
            article.tags.set([Tag.get_or_create_by_name(t) for t in data.get("tags", [])])
            for n in range(data.get("gallery", 0)):
                g, _ = store_image(make_art(500 + i * 10 + n, cat.color if cat else "#555"), caption=f"صورة {n + 1}")
                GalleryItem.objects.create(article=article, media=g, order=n, caption=f"من السوق صباحاً ({n + 1})")
        # مواد في مراحل التحرير لتجربة سير العمل
        for title, status in (
            ("مسودة: تقرير عن أسعار الإيجارات في المدن", Status.DRAFT),
            ("بانتظار المراجعة: افتتاح ملعب بلدي جديد في طولكرم", Status.IN_REVIEW),
            ("أُعيدت للتعديل: جولة في معرض الصناعات المحلية", Status.CHANGES),
        ):
            Article.objects.create(
                title=title, kind="news", status=status, category=cats.get("الضفة الغربية"), created_by=reporter,
                body="<p>نص أولي للمادة يحتاج إلى استكمال.</p>", submitted_at=now if status != Status.DRAFT else None,
            )

    def _extras(self):
        now = timezone.now()
        chief = User.objects.get(username="chief")
        for n, text in enumerate(BREAKING):
            BreakingNews.objects.create(text=text, created_by=chief, created_at=now - timedelta(minutes=20 + n * 50),
                                        send_telegram=False, send_push=False)
        live = LiveCoverage.objects.create(title=LIVE["title"], summary=LIVE["summary"], created_by=chief,
                                           started_at=now - timedelta(hours=3))
        for n, text in enumerate(LIVE["entries"]):
            LiveEntry.objects.create(coverage=live, body=text, author=chief, created_at=now - timedelta(minutes=170 - n * 40),
                                     is_important=n == 1)
        cover, _ = store_image(make_art(777, "#1c6b3a"), caption="ملف موسم الزيتون")
        dossier = Dossier.objects.create(title="ملف خاص: موسم الزيتون", cover=cover, color="#1c6b3a",
                                         description="كل ما نشرناه عن موسم الزيتون: الحصاد، والأسعار، وحكايات المزارعين.")
        dossier.articles.set(Article.objects.filter(tags__name="الزيتون"))
        HomeBlock.objects.create(kind=HomeBlock.Kind.DOSSIER, dossier=dossier, order=45, count=4)
        # كتل الواجهة التي تحتاج بيانات الموقع التجريبي: رابط القناة للترويج، ومختارات المحررين، وأعداد المتابعين
        site = SiteSettings.objects.get(pk=1)
        HomeBlock.objects.filter(kind=HomeBlock.Kind.PROMO, link="").update(link=site.telegram)
        HomeBlock.objects.filter(kind=HomeBlock.Kind.PLATFORMS).update(
            items="telegram | 1.2 مليون\nwhatsapp | 480 ألف\nx | 350 ألف\nfacebook | 2.1 مليون\ninstagram | 900 ألف\nyoutube | 610 ألف"
        )
        from arcms.core.models import HomeBlockArticle

        picks = HomeBlock.objects.filter(kind=HomeBlock.Kind.PICKS).first()
        if picks:
            chosen = Article.objects.published().filter(kind__in=["investigation", "interview", "report", "gallery"])[:5]
            HomeBlockArticle.objects.bulk_create(
                [HomeBlockArticle(block=picks, article=a, order=n) for n, a in enumerate(chosen)]
            )
        from arcms.tips.services import add_newsroom_reply, create_tip

        tip, _ = create_tip(
            subject="حفريات قرب السور ليلاً",
            body="منذ ثلاث ليالٍ تعمل آليات بعد منتصف الليل قرب الجهة الجنوبية من السور. صوّرت من نافذة البيت.",
            files=[with_fake_gps(make_art(4242, "#3b3f46"))],
        )
        add_newsroom_reply(tip, chief, "شكراً لك. هل تعرف الجهة المنفّذة؟ ولا ترسل صوراً من النافذة نفسها مرة أخرى.")

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
