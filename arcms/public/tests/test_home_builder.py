from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content.audio import AudioRejected, clean_audio
from arcms.content.models import Article, BreakingNews, Status
from arcms.core.demo_data import silent_mp3
from arcms.core.models import HomeBlock, HomeBlockArticle, SiteSettings
from arcms.core.presets import THEMES
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user


class HomeBlocksTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        HomeBlock.objects.all().delete()
        self.cat = make_category("القدس")

    def home(self):
        return self.client.get("/").content.decode()

    def test_stats_promo_platforms(self):
        site = SiteSettings.load()
        site.telegram = "https://t.me/example"
        site.whatsapp = "https://whatsapp.com/channel/x"
        site.save()
        HomeBlock.objects.create(kind="stats", title="بالأرقام", background="primary",
                                 items="84493+ | خبر منشور\n\n15+ | سنة")
        HomeBlock.objects.create(kind="promo", title="تابعونا", link="https://t.me/example", button_label="انضم")
        HomeBlock.objects.create(kind="platforms", title="منصاتنا", items="telegram | 1.2 مليون\nواتساب | 480 ألف")
        html = self.home()
        self.assertIn("84493+", html)
        self.assertIn("سنة", html)
        self.assertIn("<svg", html.split("promo-qr")[1][:300])  # رمز QR يُولَّد على الخادم
        self.assertIn("انضم", html)
        self.assertIn("1.2 مليون", html)
        self.assertIn("480 ألف", html)  # الاسم العربي للمنصة يُقبل أيضاً
        self.assertIn("platform-whatsapp", html)

    def test_promo_rejects_script_link(self):
        HomeBlock.objects.create(kind="promo", title="ترويج", link="javascript:alert(1)")
        html = self.home()
        self.assertNotIn("javascript:", html)

    def test_brief_with_breaking_and_audio(self):
        BreakingNews.objects.create(text="عاجل للموجز")
        pod = make_article("الحلقة الأولى", kind="podcast", category=self.cat)
        from django.core.files.base import ContentFile

        clean = clean_audio(silent_mp3(seconds=1))
        pod.audio.save(clean.filename, ContentFile(clean.content), save=True)
        HomeBlock.objects.create(kind="brief", title="موجز الأخبار", count=5)
        html = self.home()
        self.assertIn("عاجل للموجز", html)
        self.assertIn("<audio", html)
        self.assertIn(pod.audio.url, html)

    def test_picks_manual_order_and_published_only(self):
        a, b = make_article("أولى"), make_article("ثانية")
        draft = make_article("مسودة مختارة", status=Status.DRAFT)
        block = HomeBlock.objects.create(kind="picks", title="مختارات", layout="list")
        for n, art in enumerate((b, draft, a)):
            HomeBlockArticle.objects.create(block=block, article=art, order=n)
        html = self.home()
        self.assertLess(html.index("ثانية"), html.index("أولى"))
        self.assertNotIn("مسودة مختارة", html)

    def test_layouts_render(self):
        make_article("قصير 1", kind="short", category=self.cat)
        for layout in ("carousel", "overlay", "feature", "list", "strip", "grid"):
            cat = make_category(f"قسم {layout}")
            for i in range(4):
                make_article(f"خبر {layout} {i}", category=cat)
            HomeBlock.objects.create(kind="category", category=cat, layout=layout, title=f"كتلة {layout}")
        HomeBlock.objects.create(kind="video", article_kind="short", layout="reels", title="قصير", background="dark")
        HomeBlock.objects.create(kind="most_read", title="الأكثر قراءة", count=5)
        html = self.home()
        for marker in ("data-carousel", "card overlay", "card reel", "tabs-inline", "bg-dark", "feature-list"):
            self.assertIn(marker, html)

    def test_dark_flag_migrated_to_background(self):
        block = HomeBlock.objects.create(kind="latest", background="dark")
        self.assertTrue(block.dark)
        self.assertEqual(block.section_class, "bg-dark")


class HeaderFooterTests(ArcmsTestCase):
    def test_compact_header_cta_apps_and_radius(self):
        site = SiteSettings.load()
        site.header_style = "compact"
        site.header_cta_label = "تبرّع"
        site.header_cta_url = "/p/donate/"
        site.app_android_url = "https://play.google.com/store/apps/details?id=x"
        site.corner_style = "round"
        site.save()
        html = self.client.get("/").content.decode()
        self.assertIn("site-header", html)
        self.assertIn("compact", html)
        self.assertIn("inline-nav", html)
        self.assertIn(">تبرّع<", html)
        self.assertIn("Google Play", html)
        self.assertIn("--radius:14px", html)

    def test_unsafe_cta_hidden(self):
        site = SiteSettings.load()
        site.header_cta_label = "زر"
        site.header_cta_url = "javascript:alert(1)"
        site.save()
        self.assertNotIn("javascript:", self.client.get("/").content.decode())


class StudioBuilderTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.chief = make_user("chief", Role.CHIEF)
        login(self.client, self.chief)

    def test_block_form_saves_picks_and_new_fields(self):
        a, b = make_article("أ"), make_article("ب")
        resp = self.client.post(reverse("studio:homepage_new"), {
            "kind": "picks", "title": "مختارات", "layout": "feature", "count": 5, "order": 10, "is_active": "on",
            "background": "muted", "pick_ids": f"{b.pk},{a.pk},{b.pk},999",
        })
        self.assertEqual(resp.status_code, 302)
        block = HomeBlock.objects.get(kind="picks")
        self.assertEqual(list(HomeBlockArticle.objects.filter(block=block).values_list("article_id", flat=True)), [b.pk, a.pk])
        page = self.client.get(reverse("studio:homepage_edit", args=[block.pk]))
        self.assertContains(page, "block-field-kinds")
        self.assertContains(page, f'data-id="{b.pk}"')

    def test_block_link_validated(self):
        resp = self.client.post(reverse("studio:homepage_new"), {
            "kind": "promo", "title": "x", "layout": "grid", "count": 1, "order": 1, "link": "javascript:alert(1)",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(HomeBlock.objects.filter(kind="promo").exists())

    def test_apply_theme(self):
        login(self.client, make_user("root", Role.ADMIN))
        self.assertContains(self.client.get(reverse("studio:settings")), "مظاهر جاهزة")
        self.client.post(reverse("studio:settings"), {"apply_theme": "modern-blue"})
        site = SiteSettings.objects.get(pk=1)
        self.assertEqual(site.primary_color, THEMES["modern-blue"]["primary_color"])
        self.assertEqual(site.header_style, "compact")

    def test_podcast_audio_upload_strips_tags(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        cat = make_category()
        mp3 = SimpleUploadedFile("episode-by-reporter.mp3", silent_mp3(seconds=1), content_type="audio/mpeg")
        resp = self.client.post(reverse("studio:article_new"), {
            "kind": "podcast", "title": "حلقة", "body": "<p>نص</p>", "category": cat.pk, "priority": 0,
            "audio": mp3, "action": "save",
        }, follow=True)
        self.assertContains(resp, "نُزع من الملف الصوتي")
        article = Article.objects.get(title="حلقة")
        data = article.audio.read()
        self.assertFalse(data.startswith(b"ID3"))
        self.assertNotIn(b"Reporter", data)
        self.assertNotIn("reporter", article.audio.name)


class AudioTests(ArcmsTestCase):
    def test_clean_audio(self):
        clean = clean_audio(silent_mp3(seconds=1))
        self.assertEqual(clean.ext, "mp3")
        self.assertTrue(clean.removed)
        self.assertEqual(clean.content[:2], b"\xff\xfb")
        self.assertFalse(clean.content.endswith(b"\x00" * 125) and clean.content[-128:-125] == b"TAG")
        self.assertEqual(clean_audio(b"\x00\x00\x00\x18ftypM4A " + bytes(40)).ext, "m4a")
        with self.assertRaises(AudioRejected):
            clean_audio(b"<html>not audio</html>")


class PresetTests(ArcmsTestCase):
    def test_palestine_preset_builds_modern_home(self):
        call_command("arcms_setup", preset="palestine", theme="modern-blue", stdout=StringIO())
        kinds = list(HomeBlock.objects.values_list("kind", flat=True))
        for k in ("hero", "brief", "picks", "promo", "stats", "platforms", "most_read", "newsletter"):
            self.assertIn(k, kinds)
        self.assertEqual(SiteSettings.objects.get(pk=1).header_style, "compact")
        make_article("خبر")
        self.assertEqual(self.client.get("/").status_code, 200)


class FontTests(ArcmsTestCase):
    def test_bundled_fonts_selectable(self):
        from django.contrib.staticfiles import finders

        site = SiteSettings.load()
        site.font_headings, site.font_body = "cairo", "amiri"
        site.save()
        html = self.client.get("/").content.decode()
        self.assertIn("--font-head:Cairo,", html)
        self.assertIn("--font-body:Amiri,", html)
        self.assertIn("fonts/cairo-arabic-700-normal.woff2", html)
        from arcms.core.models import FONT_CHOICES

        for key, _ in FONT_CHOICES:
            site.font_headings = key
            self.assertTrue(finders.find(site.heading_font_file), key)
