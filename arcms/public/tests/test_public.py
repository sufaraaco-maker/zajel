import re
import xml.etree.ElementTree as ET
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from arcms.content.models import (
    Author,
    BreakingNews,
    ContactMessage,
    Dossier,
    LiveCoverage,
    LiveEntry,
    Page,
    Status,
    Tag,
)
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user
from arcms.importer.models import LegacyRedirect


class PublicSiteTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        call_command("arcms_setup", preset="palestine", stdout=StringIO())
        self.cat = make_category("القدس")
        self.author = Author.objects.create(name="سلمى الخطيب", title="مراسلة")
        with self.captureOnCommitCallbacks(execute=True):
            self.article = make_article("افتتاح مكتبة في القدس", category=self.cat, is_featured=True,
                                        subtitle="مكتبة جديدة", body="<p>نص أول.</p><p>نص ثانٍ.</p><p>ثالث.</p><p>رابع.</p>",
                                        dateline="القدس - خاص")
        self.article.authors.add(self.author)
        self.article.tags.add(Tag.get_or_create_by_name("ثقافة"))

    def test_home_renders_blocks_rtl(self):
        resp = self.client.get("/")
        self.assertContains(resp, 'dir="rtl"')
        self.assertContains(resp, "افتتاح مكتبة في القدس")
        self.assertContains(resp, "آخر الأخبار")
        self.assertIn("Content-Security-Policy", resp)
        self.assertIn("script-src 'self'", resp["Content-Security-Policy"])

    def test_no_third_party_resources_by_default(self):
        html = self.client.get(self.article.get_absolute_url()).content.decode()
        hosts = set(re.findall(r'(?:src|href)="https?://([^/"]+)', html))
        from urllib.parse import urlparse

        from django.conf import settings

        allowed = {"wa.me", "t.me", "x.com", "www.facebook.com", "testserver", urlparse(settings.SITE_URL).netloc}
        self.assertTrue(hosts <= allowed, hosts - allowed)
        self.assertNotIn("googletagmanager", html)
        self.assertNotIn("fonts.googleapis", html)

    def test_article_page(self):
        resp = self.client.get(self.article.get_absolute_url())
        self.assertContains(resp, "<h1>افتتاح مكتبة في القدس</h1>", html=False)
        self.assertContains(resp, '<span class="dateline">القدس - خاص:</span> نص أول.')
        self.assertContains(resp, "NewsArticle")
        self.assertContains(resp, 'rel="canonical"')
        self.assertContains(resp, "#ثقافة")
        self.assertContains(resp, f"/s/{self.article.pk}")
        self.assertContains(resp, 'data-article="%d"' % self.article.pk)

    def test_wrong_slug_redirects_to_canonical(self):
        resp = self.client.get(reverse("public:article", args=[self.article.pk, "قديم"]))
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], self.article.get_absolute_url())
        resp = self.client.get(f"/post/{self.article.pk}/")
        self.assertEqual(resp.status_code, 301)

    def test_short_link(self):
        resp = self.client.get(reverse("public:short", args=[self.article.pk]) + "?utm_source=telegram")
        self.assertEqual(resp.status_code, 301)
        self.assertTrue(resp["Location"].endswith("?utm_source=telegram"))

    def test_drafts_hidden_but_previewable_by_staff(self):
        draft = make_article("سري", status=Status.DRAFT, category=self.cat)
        url = draft.get_absolute_url()
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(reverse("public:short", args=[draft.pk])).status_code, 404)
        editor = make_user("ed", "editor")
        login(self.client, editor)
        resp = self.client.get(url)
        self.assertContains(resp, "معاينة داخلية")
        self.assertContains(resp, 'content="noindex"')

    def test_future_published_hidden(self):
        future = make_article("لاحقاً", category=self.cat, published_at=timezone.now() + timedelta(hours=2))
        self.assertEqual(self.client.get(future.get_absolute_url()).status_code, 404)

    def test_listings(self):
        self.assertContains(self.client.get(self.cat.get_absolute_url()), "افتتاح مكتبة")
        self.assertContains(self.client.get(reverse("public:tag", args=[Tag.objects.get(name="ثقافة").slug])), "افتتاح مكتبة")
        self.assertContains(self.client.get(self.author.get_absolute_url()), "مراسلة")
        self.assertContains(self.client.get(reverse("public:kind", args=["news"])), "افتتاح مكتبة")
        self.assertEqual(self.client.get(reverse("public:kind", args=["nonsense"])).status_code, 404)
        self.assertContains(self.client.get(reverse("public:latest")), "افتتاح مكتبة")
        d = Dossier.objects.create(title="ملف المكتبات")
        self.article.dossiers.add(d)
        self.assertContains(self.client.get(d.get_absolute_url()), "افتتاح مكتبة")

    def test_search_page_highlights(self):
        resp = self.client.get(reverse("public:search"), {"q": "المكتبات"})
        self.assertContains(resp, "<mark>مكتبة</mark>")
        self.assertContains(self.client.get(reverse("public:search"), {"q": "غير موجود إطلاقاً"}), "لا نتائج")

    def test_ticker(self):
        BreakingNews.objects.create(text="عاجل تجريبي")
        BreakingNews.objects.create(text="عاجل قديم", created_at=timezone.now() - timedelta(days=2))
        resp = self.client.get("/")
        self.assertContains(resp, "عاجل تجريبي")
        self.assertNotContains(resp, "عاجل قديم")

    def test_live_coverage_and_polling(self):
        live = LiveCoverage.objects.create(title="تغطية مباشرة")
        e1 = LiveEntry.objects.create(coverage=live, body="تحديث أول")
        self.assertContains(self.client.get(live.get_absolute_url()), "تحديث أول")
        LiveEntry.objects.create(coverage=live, body="تحديث ثانٍ")
        data = self.client.get(reverse("public:live_entries", args=[live.slug]), {"after": e1.pk}).json()
        self.assertEqual(len(data["entries"]), 1)
        self.assertIn("تحديث ثانٍ", data["entries"][0]["html"])

    def test_contact_form_stores_encrypted(self):
        page = Page.objects.get(slug="contact")
        resp = self.client.post(page.get_absolute_url(), {"name": "قارئ", "body": "معلومة حساسة"})
        self.assertContains(resp, "وصلت رسالتك")
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT body FROM content_contactmessage")
            self.assertNotIn("حساسة", cur.fetchone()[0])
        self.assertEqual(ContactMessage.objects.get().body, "معلومة حساسة")

    def test_feeds_and_sitemaps_are_valid_xml(self):
        for url in ("/rss", f"/rss/category/{self.cat.slug}", "/rss/type/news", "/sitemap.xml", "/news-sitemap.xml",
                    "/sitemap-articles.xml"):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200, url)
            ET.fromstring(resp.content)
        self.assertIn("افتتاح مكتبة", self.client.get("/rss").content.decode())
        self.assertIn("Disallow: /studio/", self.client.get("/robots.txt").content.decode())

    def test_manifest_and_service_worker(self):
        self.assertEqual(self.client.get("/manifest.webmanifest").json()["dir"], "rtl")
        sw = self.client.get("/sw.js")
        self.assertEqual(sw["Service-Worker-Allowed"], "/")

    def test_legacy_redirects(self):
        LegacyRedirect.objects.create(old_path="/2019/05/قديم", new_path=self.article.get_absolute_url())
        LegacyRedirect.objects.create(old_path="/?p=55", new_path=self.article.get_absolute_url())
        resp = self.client.get("/2019/05/%D9%82%D8%AF%D9%8A%D9%85/")
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], self.article.get_absolute_url())
        resp = self.client.get("/", {"p": "55"})
        self.assertEqual(resp.status_code, 200)  # الرئيسية موجودة فلا تحويل
        resp = self.client.get("/nothing/", {"p": "55"})
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(LegacyRedirect.objects.get(old_path="/?p=55").hits, 1)

    def test_404_page(self):
        resp = self.client.get("/لا-يوجد/")
        self.assertEqual(resp.status_code, 404)

    def test_cache_invalidated_on_publish(self):
        self.client.get("/")
        with self.captureOnCommitCallbacks(execute=True):
            make_article("خبر جديد للتو", category=self.cat, is_featured=True, priority=50)
        self.assertContains(self.client.get("/"), "خبر جديد للتو")
