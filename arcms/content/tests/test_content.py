import io
from datetime import timedelta
from unittest import mock

from django.utils import timezone
from PIL import Image

from arcms.accounts.roles import Role
from arcms.content import search
from arcms.content.imaging import ImageRejected, store_image, strip_and_normalize
from arcms.content.models import Article, ArticleRevision, EditorialNote, Status, Tag
from arcms.content.sanitize import sanitize_html
from arcms.content.workflow import WorkflowError, available_actions, can_edit, publish_due, transition
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, image_bytes, make_article, make_category, make_user


class WorkflowTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.reporter = make_user("rep", Role.REPORTER)
        self.editor = make_user("ed", Role.EDITOR)
        self.desk = make_user("desk", Role.DESK_HEAD)
        self.chief = make_user("chief", Role.CHIEF)
        self.cat = make_category("الضفة الغربية")
        self.article = make_article("مسودة", status=Status.DRAFT, category=self.cat, created_by=self.reporter)

    def actions(self, user):
        return dict(available_actions(user, self.article))

    def test_reporter_submits_but_cannot_publish(self):
        self.assertIn("submit", self.actions(self.reporter))
        self.assertNotIn("publish", self.actions(self.reporter))
        with self.assertRaises(WorkflowError):
            transition(self.article, self.reporter, "publish")
        transition(self.article, self.reporter, "submit", note="جاهزة")
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Status.IN_REVIEW)
        self.assertFalse(can_edit(self.reporter, self.article))  # لا يعدّل أثناء المراجعة

    def test_editor_reviews_desk_head_publishes(self):
        transition(self.article, self.reporter, "submit")
        self.article.refresh_from_db()
        self.assertIn("approve", self.actions(self.editor))
        self.assertNotIn("publish", self.actions(self.editor))
        with self.assertRaises(WorkflowError):
            transition(self.article, self.editor, "return", note="")  # الإعادة تتطلب سبباً
        transition(self.article, self.editor, "return", note="أضف تعقيب البلدية")
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Status.CHANGES)
        self.assertTrue(EditorialNote.objects.filter(article=self.article, kind="return").exists())
        transition(self.article, self.reporter, "submit")
        transition(self.article, self.editor, "approve")
        with mock.patch("arcms.distribution.dispatch.on_article_published") as dist:
            with self.captureOnCommitCallbacks(execute=True):
                transition(Article.objects.get(pk=self.article.pk), self.desk, "publish")
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Status.PUBLISHED)
        self.assertEqual(self.article.published_by, self.desk)
        self.assertIsNotNone(self.article.published_at)
        dist.assert_called_once_with(self.article.pk)

    def test_four_eyes_rule(self):
        own = make_article("مادتي", status=Status.DRAFT, category=self.cat, created_by=self.desk)
        self.assertNotIn("publish", dict(available_actions(self.desk, own)))
        # رئيس التحرير يستطيع النشر الفوري (العاجل)
        chief_own = make_article("مادة رئيس التحرير", status=Status.DRAFT, category=self.cat, created_by=self.chief)
        self.assertIn("publish", dict(available_actions(self.chief, chief_own)))
        site = SiteSettings.load()
        site.require_review = False
        site.save()
        self.assertIn("publish", dict(available_actions(self.desk, own)))

    def test_desk_scope(self):
        other = make_category("غزة")
        self.desk.desks.add(self.cat)
        foreign = make_article("خارج القسم", status=Status.IN_REVIEW, category=other, created_by=self.reporter)
        self.assertEqual(dict(available_actions(self.desk, foreign)), {})
        self.assertFalse(can_edit(self.desk, foreign))

    def test_schedule_and_worker_publishes(self):
        transition(self.article, self.reporter, "submit")
        when = timezone.now() + timedelta(hours=1)
        with self.assertRaises(WorkflowError):
            transition(Article.objects.get(pk=self.article.pk), self.chief, "schedule", when=timezone.now() - timedelta(minutes=1))
        transition(Article.objects.get(pk=self.article.pk), self.chief, "schedule", when=when)
        self.assertEqual(publish_due(), [])
        with mock.patch("arcms.distribution.dispatch.on_article_published"):
            with self.captureOnCommitCallbacks(execute=True):
                published = publish_due(now=when + timedelta(seconds=5))
        self.assertEqual(published, [self.article.pk])
        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Status.PUBLISHED)
        self.assertEqual(self.article.published_at, when)
        self.assertTrue(self.article.is_live or self.article.published_at > timezone.now())

    def test_unpublish(self):
        live = make_article("منشورة", category=self.cat, created_by=self.reporter)
        self.assertNotIn("unpublish", dict(available_actions(self.editor, live)))
        transition(live, self.desk, "unpublish", note="خطأ في المعلومة")
        live.refresh_from_db()
        self.assertEqual(live.status, Status.UNPUBLISHED)
        self.assertFalse(Article.objects.published().filter(pk=live.pk).exists())

    def test_revision_captured_and_deduplicated(self):
        ArticleRevision.capture(self.article, self.reporter)
        ArticleRevision.capture(self.article, self.reporter)
        self.assertEqual(self.article.revisions.count(), 1)
        self.article.title = "عنوان جديد"
        self.article.save()
        ArticleRevision.capture(self.article, self.reporter)
        self.assertEqual(self.article.revisions.count(), 2)

    def test_source_notes_encrypted(self):
        self.article.source_notes = "المصدر: موظف في البلدية"
        self.article.save()
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT source_notes FROM content_article WHERE id = %s", [self.article.pk])
            raw = cur.fetchone()[0]
        self.assertNotIn("البلدية", raw)
        self.assertEqual(Article.objects.get(pk=self.article.pk).source_notes, "المصدر: موظف في البلدية")


class ImagingTests(ArcmsTestCase):
    def test_gps_and_camera_metadata_stripped(self):
        raw = image_bytes(gps=True)
        self.assertIn(0x8825, Image.open(io.BytesIO(raw)).getexif())
        clean = strip_and_normalize(raw)
        self.assertIn("إحداثيات الموقع الجغرافي (GPS)", clean.removed)
        self.assertIn("الرقم التسلسلي للكاميرا", clean.removed)
        out = Image.open(io.BytesIO(clean.content))
        self.assertEqual(len(out.getexif()), 0)
        self.assertNotIn("exif", out.info)
        self.assertNotIn(b"PhoneMaker", clean.content)
        self.assertNotIn(b"SERIAL-123", clean.content)

    def test_orientation_applied_before_strip(self):
        img = Image.new("RGB", (400, 200), (10, 120, 200))
        exif = img.getexif()
        exif[0x0112] = 6  # تدوير 90 درجة
        buf = io.BytesIO()
        img.save(buf, "JPEG", exif=exif)
        clean = strip_and_normalize(buf.getvalue())
        self.assertEqual((clean.width, clean.height), (200, 400))

    def test_png_with_text_chunks(self):
        from PIL import PngImagePlugin

        img = Image.new("RGBA", (50, 50), (0, 0, 0, 128))
        info = PngImagePlugin.PngInfo()
        info.add_text("Author", "Secret Photographer")
        buf = io.BytesIO()
        img.save(buf, "PNG", pnginfo=info)
        clean = strip_and_normalize(buf.getvalue())
        self.assertEqual(clean.mime, "image/png")
        self.assertNotIn(b"Secret Photographer", clean.content)

    def test_rejects_non_images(self):
        with self.assertRaises(ImageRejected):
            strip_and_normalize(b"<svg onload=alert(1)></svg>")
        with self.assertRaises(ImageRejected):
            strip_and_normalize(b"%PDF-1.4 fake")

    def test_store_image_renditions_and_dedupe(self):
        user = make_user("photog", Role.REPORTER)
        asset, created = store_image(image_bytes(gps=True, size=(1600, 900)), user=user, caption="تعليق")
        self.assertTrue(created)
        self.assertEqual(set(asset.renditions), {"thumb", "card", "large", "social"})
        self.assertNotIn("IMG", asset.file.name)
        again, created2 = store_image(image_bytes(gps=True, size=(1600, 900)), user=user)
        self.assertFalse(created2)
        self.assertEqual(again.pk, asset.pk)
        social = Image.open(asset.file.storage.open(asset.renditions["social"]))
        self.assertEqual(social.size, (1200, 630))


class SanitizeTests(ArcmsTestCase):
    def test_scripts_and_handlers_removed(self):
        out = sanitize_html('<p onclick="x()">نص</p><script>alert(1)</script><a href="javascript:alert(1)">رابط</a>')
        self.assertNotIn("script", out)
        self.assertNotIn("onclick", out)
        self.assertNotIn("javascript", out)
        self.assertIn("<p>نص</p>", out)

    def test_only_safe_video_iframes(self):
        out = sanitize_html('<iframe class="ql-video" src="https://www.youtube.com/embed/aqz-KE-bpKQ"></iframe>'
                            '<iframe src="https://tracker.example/x"></iframe>')
        self.assertIn("youtube-nocookie.com/embed/aqz-KE-bpKQ", out)
        self.assertNotIn("tracker.example", out)
        self.assertEqual(out.count("<iframe"), 1)

    def test_plain_text_keeps_sentence_boundaries(self):
        from arcms.content.sanitize import plain_text

        self.assertEqual(plain_text("<p>جملة أولى.</p><p>جملة ثانية.</p>"), "جملة أولى. جملة ثانية.")

    def test_links_get_rel(self):
        out = sanitize_html('<a href="https://example.org" target="_blank">x</a>')
        self.assertIn('rel="noopener noreferrer nofollow"', out)


class TagTests(ArcmsTestCase):
    def test_hamza_variants_share_tag(self):
        a = Tag.get_or_create_by_name("#الأسرى")
        b = Tag.get_or_create_by_name("الاسرى")
        self.assertEqual(a.pk, b.pk)
        self.assertEqual(a.name, "الأسرى")


class SearchTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.cat = make_category()
        with self.captureOnCommitCallbacks(execute=True):
            self.a1 = make_article("قوات تشن حملة بالاعتقال في نابلس", body="<p>وقال مسئول إن الاعتقالات مستمرة.</p>", category=self.cat)
            self.a2 = make_article("افتتاح مكتبة عامة في غزّة", body="<p>مكتبة جديدة بغزة تضم آلاف الكتب.</p>", category=self.cat)
            self.draft = make_article("مسودة عن الاعتقال", status=Status.DRAFT, category=self.cat)

    def ids(self, q, **kw):
        return [a.pk for a in search.search_articles(q, **kw).articles]

    def test_stemming(self):
        self.assertEqual(self.ids("اعتقال"), [self.a1.pk])
        self.assertEqual(self.ids("الاعتقالات"), [self.a1.pk])

    def test_hamza_and_diacritics(self):
        self.assertEqual(self.ids("مسؤول"), [self.a1.pk])
        self.assertEqual(self.ids("غزة"), [self.a2.pk])
        self.assertEqual(self.ids("مَكْتَبَة"), [self.a2.pk])

    def test_drafts_hidden_from_public_but_visible_in_studio(self):
        self.assertNotIn(self.draft.pk, self.ids("اعتقال"))
        self.assertIn(self.draft.pk, self.ids("اعتقال", public=False))

    def test_and_semantics_and_phrases(self):
        self.assertEqual(self.ids("مكتبة نابلس"), [])
        self.assertEqual(self.ids("«مكتبة عامة»"), [self.a2.pk])
        self.assertEqual(self.ids("«عامة مكتبة»"), [])

    def test_title_ranks_above_body(self):
        with self.captureOnCommitCallbacks(execute=True):
            body_only = make_article("خبر آخر", body="<p>ذُكرت مكتبة في النص فقط.</p>", category=self.cat)
        ids = self.ids("مكتبة")
        self.assertEqual(ids[0], self.a2.pk)
        self.assertIn(body_only.pk, ids)

    def test_reindex_after_edit_and_delete(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.a2.title = "افتتاح متحف في غزة"
            self.a2.body = "<p>متحف</p>"
            self.a2.save()
        self.assertEqual(self.ids("مكتبة"), [])
        self.assertEqual(self.ids("متحف"), [self.a2.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.a2.delete()
        self.assertEqual(self.ids("متحف"), [])

    def test_tags_are_searchable(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.a2.tags.add(Tag.get_or_create_by_name("ثقافة"))
        self.assertEqual(self.ids("ثقافة"), [self.a2.pk])

    def test_empty_and_symbol_queries(self):
        self.assertEqual(self.ids(""), [])
        self.assertEqual(self.ids("'; DROP TABLE content_article; --"), [])
        self.assertTrue(Article.objects.exists())
