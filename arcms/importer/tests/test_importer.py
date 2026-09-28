import json
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command

from arcms.audit.models import Action, AuditEntry
from arcms.content import search
from arcms.content.models import Article, Author, Category, MediaAsset, Status
from arcms.core.testing import ArcmsTestCase, image_bytes
from arcms.distribution.models import Delivery
from arcms.importer.loader import Loader
from arcms.importer.models import LegacyRedirect
from arcms.importer.sources import read_jsonl, read_wxr, wordpress_to_html

WXR = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:excerpt="http://wordpress.org/export/1.2/excerpt/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:wp="http://wordpress.org/export/1.2/">
<channel>
  <title>أرشيف قديم</title>
  <wp:author><wp:author_login>ahmad</wp:author_login><wp:author_display_name><![CDATA[أحمد يوسف]]></wp:author_display_name></wp:author>
  <item>
    <title>حملة اعتقالات في جنين</title>
    <link>https://old.example.org/2019/05/%D8%AD%D9%85%D9%84%D8%A9/</link>
    <pubDate>Tue, 14 May 2019 09:00:00 +0000</pubDate>
    <dc:creator><![CDATA[ahmad]]></dc:creator>
    <content:encoded><![CDATA[قالت مصادر محلية إن قوات شنت حملة.

[caption id="x"]<img src="https://old.example.org/img.jpg"> صورة من الموقع[/caption]

وتم الإفراج عن بعضهم.<script>bad()</script>]]></content:encoded>
    <excerpt:encoded><![CDATA[ملخص الخبر]]></excerpt:encoded>
    <wp:post_id>101</wp:post_id>
    <wp:post_date_gmt>2019-05-14 09:00:00</wp:post_date_gmt>
    <wp:post_name>حملة</wp:post_name>
    <wp:status>publish</wp:status>
    <wp:post_type>post</wp:post_type>
    <category domain="category" nicename="jenin"><![CDATA[جنين]]></category>
    <category domain="category" nicename="news"><![CDATA[أخبار]]></category>
    <category domain="post_tag" nicename="a"><![CDATA[الأسرى]]></category>
    <wp:postmeta><wp:meta_key>_thumbnail_id</wp:meta_key><wp:meta_value>500</wp:meta_value></wp:postmeta>
  </item>
  <item>
    <title>في وداع صديق</title>
    <link>https://old.example.org/?p=102</link>
    <dc:creator><![CDATA[ahmad]]></dc:creator>
    <content:encoded><![CDATA[<p>مقال رأي.</p>]]></content:encoded>
    <wp:post_id>102</wp:post_id>
    <wp:post_date_gmt>2019-06-01 10:00:00</wp:post_date_gmt>
    <wp:status>publish</wp:status>
    <wp:post_type>post</wp:post_type>
    <category domain="category" nicename="op"><![CDATA[مقالات الرأي]]></category>
  </item>
  <item>
    <title>مسودة قديمة</title>
    <content:encoded><![CDATA[نص]]></content:encoded>
    <wp:post_id>103</wp:post_id>
    <wp:status>draft</wp:status>
    <wp:post_type>post</wp:post_type>
  </item>
  <item>
    <title>صفحة</title><wp:post_id>104</wp:post_id><wp:status>publish</wp:status><wp:post_type>page</wp:post_type>
  </item>
  <item>
    <title>img</title><wp:post_id>500</wp:post_id><wp:post_type>attachment</wp:post_type>
    <wp:attachment_url>https://old.example.org/uploads/photo.jpg</wp:attachment_url>
  </item>
</channel>
</rss>
"""


class ImporterTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.dir = Path(tempfile.mkdtemp())
        self.wxr = self.dir / "export.xml"
        self.wxr.write_text(WXR, encoding="utf-8")

    def test_wxr_parsing(self):
        records = list(read_wxr(self.wxr))
        self.assertEqual([r.legacy_id for r in records], ["wp:101", "wp:102", "wp:103"])
        first = records[0]
        self.assertEqual(first.authors, ["أحمد يوسف"])
        self.assertEqual(first.categories, ["جنين", "أخبار"])
        self.assertEqual(first.tags, ["الأسرى"])
        self.assertEqual(first.image_url, "https://old.example.org/uploads/photo.jpg")
        self.assertIn("<figcaption>", first.body_html)
        self.assertEqual(first.published_at.year, 2019)
        self.assertEqual(records[2].status, "draft")

    def test_wordpress_autop(self):
        html = wordpress_to_html("سطر أول\n\nسطر ثانٍ\nمع كسر")
        self.assertEqual(html, "<p>سطر أول</p><p>سطر ثانٍ<br>مع كسر</p>")

    def test_import_creates_articles_redirects_and_is_idempotent(self):
        with self.captureOnCommitCallbacks(execute=True):
            run = Loader(source="wxr", filename="export.xml").load(read_wxr(self.wxr))
        self.assertEqual((run.created, run.updated, run.skipped), (3, 0, 0))
        article = Article.objects.get(legacy_id="wp:101")
        self.assertEqual(article.status, Status.PUBLISHED)
        self.assertEqual(article.category.name, "جنين")
        self.assertEqual(article.extra_categories.get().name, "أخبار")
        self.assertNotIn("script", article.body)
        self.assertEqual(article.authors.get().name, "أحمد يوسف")
        self.assertEqual(Article.objects.get(legacy_id="wp:102").kind, "opinion")
        self.assertEqual(Article.objects.get(legacy_id="wp:103").status, Status.DRAFT)
        self.assertIsNotNone(article.distributed_at)  # الأرشيف لا يُرسل للقنوات
        self.assertFalse(Delivery.objects.exists())
        # الروابط القديمة
        redirect = LegacyRedirect.objects.get(old_path="/2019/05/حملة")
        self.assertEqual(redirect.new_path, article.get_absolute_url())
        self.assertTrue(LegacyRedirect.objects.filter(old_path="/?p=102").exists())
        resp = self.client.get("/2019/05/%D8%AD%D9%85%D9%84%D8%A9/")
        self.assertEqual(resp.status_code, 301)
        # البحث العربي يعمل على الأرشيف
        self.assertIn(article, search.search_articles("اعتقال").articles)
        # قيد تدقيق واحد موجز بدل آلاف القيود
        self.assertEqual(AuditEntry.objects.filter(action=Action.IMPORT).count(), 1)
        self.assertFalse(AuditEntry.objects.filter(object_type="content.article").exists())
        # إعادة الاستيراد تحدّث ولا تكرر
        run2 = Loader(source="wxr").load(read_wxr(self.wxr))
        self.assertEqual((run2.created, run2.updated), (0, 3))
        self.assertEqual(Article.objects.count(), 3)
        self.assertEqual(Author.objects.count(), 1)
        self.assertEqual(Category.objects.filter(name="جنين").count(), 1)

    def test_download_media_strips_metadata(self):
        resp = mock.Mock()
        resp.raise_for_status = mock.Mock()
        resp.raw.read.return_value = image_bytes(gps=True)
        with mock.patch("requests.Session.get", return_value=resp):
            Loader(source="wxr", download_media=True).load(read_wxr(self.wxr))
        article = Article.objects.get(legacy_id="wp:101")
        self.assertIsNotNone(article.featured_image)
        self.assertIn("إحداثيات الموقع الجغرافي (GPS)", article.featured_image.removed_metadata)
        self.assertEqual(MediaAsset.objects.count(), 1)  # الصورة نفسها لا تُنزَّل مرتين
        self.assertIn(article.featured_image.rendition("large"), article.body)

    def test_dry_run_saves_nothing(self):
        run = Loader(source="wxr", dry_run=True).load(read_wxr(self.wxr))
        self.assertEqual(run.created, 3)
        self.assertFalse(Article.objects.exists())

    def test_jsonl_and_command(self):
        path = self.dir / "archive.jsonl"
        rows = [
            {"id": 1, "title": "تقرير عن المياه", "body": "<p>نص</p>", "kind": "report", "category": "بيئة",
             "tags": ["مياه"], "author": "ليلى", "published_at": "2020-01-02T08:00:00Z", "url": "/news/1.html"},
            {"id": 2, "title": "مسودة", "status": "draft"},
        ]
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
        self.assertEqual(len(list(read_jsonl(path))), 2)
        call_command("arcms_import", "jsonl", str(path), stdout=StringIO())
        report = Article.objects.get(legacy_id="json:1")
        self.assertEqual(report.kind, "report")
        self.assertEqual(report.published_at.year, 2020)
        self.assertTrue(LegacyRedirect.objects.filter(old_path="/news/1.html").exists())
