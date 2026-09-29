import hashlib
import io
import tempfile
from pathlib import Path
from unittest import mock

import requests
from django.core.management import call_command
from django.test import override_settings
from PIL import Image

from arcms.content.models import Article, Category, Page
from arcms.core.demo_brands import BRANDS
from arcms.core.demo_photos import CommonsPhotos, Photo, acceptable, credits_html, query_for
from arcms.core.models import HomeBlock, MenuItem, SiteSettings
from arcms.core.testing import ArcmsTestCase


def small_jpeg(color=(40, 90, 150)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), color).save(buf, "JPEG")
    return buf.getvalue()


def page(title, *, license="CC BY-SA 4.0", width=2000, height=1300, mime="image/jpeg", restrictions="",
         categories="Markets", index=1):
    meta = {
        "Artist": {"value": '<a href="//commons.wikimedia.org/wiki/User:Photographer">Photographer</a>'},
        "LicenseShortName": {"value": license},
        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"},
        "Categories": {"value": categories},
        "Restrictions": {"value": restrictions},
    }
    return {"title": title, "index": index, "imageinfo": [{
        "mime": mime, "width": width, "height": height, "url": f"https://upload.example/{index}.jpg",
        "thumburl": f"https://upload.example/thumb/{index}.jpg",
        "descriptionurl": f"https://commons.wikimedia.org/wiki/{title}", "extmetadata": meta,
    }]}


class FakeResponse:
    def __init__(self, *, json_data=None, content=b"", status=200):
        self._json, self.content, self.status_code = json_data, content, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))

    def json(self):
        return self._json


class FakeCommons:
    """يحاكي واجهة كومنز: لكل بحث ثلاث نتائج، وأي رابط صورة يعيد JPEG صغيراً."""

    def __init__(self):
        self.headers = {}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(url)
        if params:
            q = params["gsrsearch"].removesuffix(" filetype:bitmap")
            return FakeResponse(json_data={"query": {"pages": [
                page(f"File:{q} {n}.jpg", index=n) for n in (2, 1, 3)
            ]}})
        return FakeResponse(content=small_jpeg(tone(url)))


def tone(key) -> tuple[int, int, int]:
    """لون مختلف لكل مفتاح، فلا تتطابق الصور فتُدمج عند الحفظ."""
    digest = hashlib.sha256(str(key).encode()).digest()
    return digest[0], digest[1], digest[2]


class DownError:
    headers = {}

    def get(self, *a, **k):
        raise requests.ConnectionError("no route")


class LicenseFilterTests(ArcmsTestCase):
    def test_free_licenses_only(self):
        for lic in ("CC BY-SA 4.0", "CC BY 2.0", "CC0", "Public domain", "CC-BY-SA-3.0"):
            self.assertTrue(acceptable(page("File:a.jpg", license=lic)), lic)
        for lic in ("CC BY-NC 2.0", "CC BY-ND 4.0", "GFDL", "Fair use", ""):
            self.assertFalse(acceptable(page("File:a.jpg", license=lic)), lic)

    def test_shape_mime_restrictions_and_topics(self):
        self.assertFalse(acceptable(page("File:small.jpg", width=800, height=500)))
        self.assertFalse(acceptable(page("File:square.jpg", width=2000, height=2000)))
        self.assertTrue(acceptable(page("File:tall.jpg", width=1200, height=1800), tall=True))
        self.assertFalse(acceptable(page("File:a.png", mime="image/png")))
        self.assertFalse(acceptable(page("File:a.jpg", restrictions="personality")))
        self.assertFalse(acceptable(page("File:Soldiers at the market.jpg")))
        self.assertFalse(acceptable(page("File:Market.jpg", categories="Protests in 2020")))
        self.assertTrue(acceptable(page("File:Water tanker truck.jpg")))

    def test_query_choice(self):
        self.assertEqual(query_for({"title": "x", "kind": "news", "photo": "olive trees"}), "olive trees")
        self.assertEqual(query_for({"title": "x", "kind": "infographic", "photo": "olive trees"}), "")
        self.assertEqual(query_for({"title": "x", "kind": "podcast"}), "microphone studio")
        self.assertEqual(query_for({"title": "x", "kind": "news", "category": "رياضة"}), "stadium")


class CommonsPhotosTests(ArcmsTestCase):
    def test_search_order_uniqueness_credit_and_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeCommons()
            photos = CommonsPhotos(Path(tmp), session=fake)
            first = photos.get("olive harvest")
            second = photos.get("olive harvest")
            self.assertEqual(first.title, "File:olive harvest 1.jpg")  # ترتيب الصلة لا ترتيب الرد
            self.assertEqual(second.title, "File:olive harvest 2.jpg")  # لا تتكرر صورة في موقع واحد
            self.assertEqual(first.author, "Photographer")
            self.assertEqual(first.credit, "Photographer / ويكيميديا كومنز (CC BY-SA 4.0)")
            self.assertIn("arcms-demo", fake.headers["User-Agent"])
            # المرة الثانية من المجلد المحلي دون أي طلب
            offline = CommonsPhotos(Path(tmp), session=DownError())
            again = offline.get("olive harvest")
            self.assertEqual(again.title, "File:olive harvest 1.jpg")
            self.assertEqual(again.data, first.data)

    def test_offline_falls_back_quietly(self):
        messages = []
        with tempfile.TemporaryDirectory() as tmp:
            photos = CommonsPhotos(Path(tmp), session=DownError(), log=messages.append)
            for q in ("a", "b", "c", "d"):
                self.assertIsNone(photos.get(q))
            self.assertTrue(photos.offline)
            self.assertEqual(len(messages), 1)

    def test_rejects_non_jpeg_download(self):
        class HtmlInsteadOfImage(FakeCommons):
            def get(self, url, params=None, timeout=None):
                if params:
                    return super().get(url, params, timeout)
                return FakeResponse(content=b"<html>blocked</html>")

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(CommonsPhotos(Path(tmp), session=HtmlInsteadOfImage()).get("market"))

    def test_credits_page_escapes_and_links_https_only(self):
        photos = [
            Photo(b"", "File:<b>x</b>.jpg", "A <script>", "CC BY 4.0", "https://creativecommons.org/licenses/by/4.0",
                  "https://commons.wikimedia.org/wiki/File:x.jpg"),
            Photo(b"", "File:y.jpg", "B", "CC0", "javascript:alert(1)", "http://insecure.example/y"),
        ]
        out = credits_html(photos)
        self.assertNotIn("<script>", out)
        self.assertNotIn("javascript:", out)
        self.assertNotIn("http://insecure", out)
        self.assertIn('href="https://commons.wikimedia.org/wiki/File:x.jpg"', out)


def quick_art(seed, color, tall=False):
    return small_jpeg(tone(("art", seed)))


@mock.patch("arcms.core.management.commands.arcms_demo.make_art", quick_art)
class BrandDemoTests(ArcmsTestCase):
    def build(self, brand, *extra):
        tmp = tempfile.mkdtemp()
        with override_settings(BASE_DIR=Path(tmp)):
            call_command("arcms_demo", "--force", "--no-traffic", "--brand", brand, "--photos-cache", tmp, *extra,
                         stdout=io.StringIO())

    def test_general_brand_identity_and_content(self):
        self.build("sonbola", "--no-photos")
        site = SiteSettings.objects.get(pk=1)
        spec = BRANDS["sonbola"]
        self.assertEqual(site.name, "سنبلة نيوز")
        self.assertEqual(site.primary_color, "#1f7a3e")
        self.assertEqual(site.header_style, "classic")
        self.assertEqual(site.logo_height, 62)
        self.assertTrue(site.logo and site.logo_dark)
        self.assertEqual(site.logo.mime, "image/png")
        self.assertFalse(Category.objects.filter(name="الأسرى").exists())
        self.assertFalse(Article.objects.filter(category__name__in=["القدس", "الأسرى", "شؤون إسرائيلية"]).exists())
        lead = Article.objects.published().filter(is_featured=True).order_by("-priority").first()
        self.assertEqual(lead.title, spec["articles"][0]["title"])
        self.assertEqual(HomeBlock.objects.filter(kind="hero").get().layout, "feature")
        self.assertEqual(HomeBlock.objects.filter(kind="promo").get().link, site.whatsapp)
        self.assertFalse(Page.objects.filter(slug="photo-credits").exists())
        resp = self.client.get("/")
        self.assertContains(resp, "logo-dark")
        self.assertContains(resp, "--logo-h:62px")

    def test_real_photos_with_credits_page(self):
        with mock.patch("arcms.core.demo_photos.requests.Session", FakeCommons):
            self.build("ofoq")
        lead = Article.objects.get(title=BRANDS["ofoq"]["articles"][0]["title"])
        self.assertIn("ويكيميديا كومنز", lead.featured_image.credit)
        infographic = Article.objects.filter(kind="infographic").first()
        self.assertNotIn("ويكيميديا", infographic.featured_image.credit)
        credits = Page.objects.get(slug="photo-credits")
        self.assertIn("https://commons.wikimedia.org/wiki/", credits.body)
        self.assertTrue(MenuItem.objects.filter(location="footer", page=credits).exists())
        self.assertEqual(SiteSettings.objects.get(pk=1).header_color, "#1d1a1b")
