import io
from unittest import mock

from django.core.files.storage import default_storage
from django.urls import reverse
from PIL import Image

from arcms.accounts.roles import Role
from arcms.content import cards
from arcms.content.imaging import store_image
from arcms.content.models import BreakingNews, Status
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user
from arcms.distribution.models import Channel, Delivery


def photo_bytes(color=(30, 120, 200), size=(1600, 900)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def logo_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (400, 160), (20, 40, 200, 255)).save(buf, "PNG")
    return buf.getvalue()


class RenderTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.site = SiteSettings.load()

    def test_sizes_and_fonts(self):
        image, _ = store_image(photo_bytes())
        article = make_article("افتتاح مكتبة عامة جديدة في المدينة القديمة تضم أكثر من 20 ألف كتاب",
                               featured_image=image, category=make_category("ثقافة"))
        for fmt, size in cards.SIZES.items():
            data = cards.render_article(article, self.site, fmt)
            self.assertEqual(Image.open(io.BytesIO(data)).size, size)
        for key in cards.FONT_FILES:  # كل خطوط الموقع السبعة متاحة للبطاقات
            self.site.font_headings = key
            self.assertTrue(cards.render_breaking("عاجل: خبر للتجربة 2026", None, self.site, "wide"))

    def test_long_headline_is_truncated_to_fit(self):
        font, size, lines = cards.fit("كلمة " * 120, "plex", 1080, 3, 64, 40)
        self.assertEqual(len(lines), 3)
        self.assertEqual(size, 40)
        self.assertTrue(lines[-1].endswith("…"))
        for line in lines:
            self.assertLessEqual(font.getlength(line, direction="rtl", language="ar"), 1080)

    def test_short_headline_keeps_big_font(self):
        _, size, lines = cards.fit("خبر قصير", "kufi", 1080, 3, 64, 40)
        self.assertEqual((size, len(lines)), (64, 1))

    def test_logo_and_no_photo(self):
        logo, _ = store_image(logo_bytes())
        self.site.logo = logo
        article = make_article("بلا صورة")
        data = cards.render_article(article, self.site, "square")
        self.assertEqual(Image.open(io.BytesIO(data)).size, (1080, 1080))

    def test_version_changes_with_title_and_identity(self):
        article = make_article("العنوان الأول")
        v1 = cards.article_version(article, self.site)
        article.title = "العنوان الثاني"
        self.assertNotEqual(v1, cards.article_version(article, self.site))
        v2 = cards.article_version(article, self.site)
        self.site.primary_color = "#123456"
        self.site.save()
        self.assertNotEqual(v2, cards.article_version(article, SiteSettings.objects.get(pk=self.site.pk)))

    def test_cache_replaces_old_versions(self):
        article = make_article("نسخة أولى")
        cards.article_card(article, self.site)
        article.title = "نسخة ثانية"
        article.save()
        cards.article_card(article, self.site)
        _, files = default_storage.listdir("cards")
        mine = [f for f in files if f.startswith(f"a{article.pk}-wide-")]
        self.assertEqual(len(mine), 1)


class PublicCardTests(ArcmsTestCase):
    def test_published_only_and_og_tag(self):
        article = make_article("خبر منشور")
        draft = make_article("مسودة سرية", status=Status.DRAFT)
        resp = self.client.get(reverse("public:article_card", args=[article.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertIn("max-age", resp["Cache-Control"])
        self.assertEqual(self.client.get(reverse("public:article_card", args=[draft.pk])).status_code, 404)
        page = self.client.get(article.get_absolute_url())
        self.assertContains(page, f'/card/a/{article.pk}.jpg?v=')

    def test_disabled_setting_falls_back(self):
        SiteSettings.objects.filter(pk=SiteSettings.load().pk).update(share_cards=False)
        SiteSettings.forget_local()
        from django.core.cache import cache

        cache.clear()
        article = make_article("بلا بطاقة")
        self.assertEqual(self.client.get(reverse("public:article_card", args=[article.pk])).status_code, 404)
        self.assertNotContains(self.client.get(article.get_absolute_url()), "/card/a/")

    def test_without_raqm_nothing_breaks(self):
        article = make_article("خادم بلا raqm")
        with mock.patch("arcms.content.cards.available", return_value=False):
            self.assertEqual(self.client.get(reverse("public:article_card", args=[article.pk])).status_code, 404)
            self.assertNotContains(self.client.get(article.get_absolute_url()), "/card/a/")

    def test_breaking_card_active_only(self):
        item = BreakingNews.objects.create(text="عاجل للتجربة", send_telegram=False, send_push=False)
        self.assertEqual(self.client.get(reverse("public:breaking_card", args=[item.pk])).status_code, 200)
        item.is_active = False
        item.save()
        self.assertEqual(self.client.get(reverse("public:breaking_card", args=[item.pk])).status_code, 404)


class StudioCardTests(ArcmsTestCase):
    def test_editor_preview_and_download_draft_not_cached(self):
        user = make_user("writer", Role.REPORTER)
        login(self.client, user)
        draft = make_article("مسودة للمعاينة", status=Status.DRAFT, created_by=user)
        self.assertContains(self.client.get(reverse("studio:article_edit", args=[draft.pk])), "بطاقة المشاركة")
        resp = self.client.get(reverse("studio:article_card", args=[draft.pk]), {"f": "story", "download": "1"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("attachment", resp["Content-Disposition"])
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (1080, 1920))
        try:
            _, files = default_storage.listdir("cards")
        except FileNotFoundError:
            files = []
        self.assertFalse([f for f in files if f.startswith(f"a{draft.pk}-")])
        other = make_article("مسودة زميل", status=Status.DRAFT, created_by=make_user("other", Role.REPORTER))
        self.assertEqual(self.client.get(reverse("studio:article_card", args=[other.pk])).status_code, 403)

    def test_breaking_card_download(self):
        login(self.client, make_user("desk", Role.DESK_HEAD))
        item = BreakingNews.objects.create(text="عاجل", send_telegram=False, send_push=False)
        resp = self.client.get(reverse("studio:breaking_card", args=[item.pk]), {"f": "square"})
        self.assertEqual(Image.open(io.BytesIO(resp.content)).size, (1080, 1080))
        self.assertContains(self.client.get(reverse("studio:breaking")), "بطاقة")


class DistributionCardTests(ArcmsTestCase):
    def test_telegram_gets_card_for_article_and_breaking(self):
        from arcms.distribution.tasks import _content

        article = make_article("خبر للقناة")
        delivery = Delivery.objects.create(article=article, channel=Channel.TELEGRAM, target="@x")
        self.assertIn(f"/card/a/{article.pk}.jpg?v=", _content(delivery)["image"])
        item = BreakingNews.objects.create(text="عاجل للقناة", send_telegram=False, send_push=False)
        delivery = Delivery.objects.create(breaking=item, channel=Channel.TELEGRAM, target="@x")
        self.assertIn(f"/card/b/{item.pk}.jpg?v=", _content(delivery)["image"])
