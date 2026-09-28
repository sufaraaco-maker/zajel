from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.analytics.collector import record_view
from arcms.audit.models import Action, AuditEntry
from arcms.content.models import Status, Tag
from arcms.content.tags import merge_tags, similar_groups, tag_key
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user
from arcms.distribution.models import NewsletterSubscriber


class TagToolsTests(ArcmsTestCase):
    def test_similar_tags_grouped_by_arabic_stem(self):
        a = Tag.get_or_create_by_name("الاعتقال")
        b = Tag.get_or_create_by_name("اعتقالات")
        c = Tag.get_or_create_by_name("الإعتقالات")
        Tag.get_or_create_by_name("غزة")
        self.assertEqual(tag_key(a), tag_key(b))
        self.assertEqual(tag_key(a), tag_key(c))
        groups = similar_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual({t.pk for t in groups[0]}, {a.pk, b.pk, c.pk})

    def test_merge_moves_articles_and_redirects(self):
        keep = Tag.get_or_create_by_name("الأسرى")
        dup = Tag.objects.create(name="أسرى_فلسطين")
        article = make_article("خبر")
        article.tags.add(dup)
        old_url = dup.get_absolute_url()
        self.assertEqual(merge_tags(dup, keep), 1)
        self.assertFalse(Tag.objects.filter(pk=dup.pk).exists())
        self.assertIn(keep, article.tags.all())
        resp = self.client.get(old_url)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp["Location"], keep.get_absolute_url())
        with self.assertRaises(ValueError):
            merge_tags(keep, keep)

    def test_merge_views(self):
        chief = make_user("chief", Role.CHIEF)
        login(self.client, chief)
        a = Tag.get_or_create_by_name("الاعتقال")
        b = Tag.get_or_create_by_name("اعتقالات")
        resp = self.client.get(reverse("studio:tags_list"))
        self.assertContains(resp, "وسوم متشابهة")
        self.client.post(reverse("studio:tag_merge_group"), {"tag": [a.pk, b.pk], "target": a.pk})
        self.assertEqual(list(Tag.objects.values_list("pk", flat=True)), [a.pk])
        c = Tag.get_or_create_by_name("غزة")
        self.assertContains(self.client.get(reverse("studio:tags_edit", args=[c.pk])), "دمج هذا الوسم")
        self.client.post(reverse("studio:tag_merge", args=[c.pk]), {"target": a.pk})
        self.assertFalse(Tag.objects.filter(pk=c.pk).exists())

    def test_reporter_cannot_merge(self):
        reporter = make_user("rep", Role.REPORTER)
        login(self.client, reporter)
        tag = Tag.get_or_create_by_name("وسم")
        other = Tag.get_or_create_by_name("آخر")
        self.assertEqual(self.client.post(reverse("studio:tag_merge", args=[tag.pk]), {"target": other.pk}).status_code, 403)


class SubscriberToolsTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.social = make_user("social", Role.SOCIAL)
        login(self.client, self.social)
        NewsletterSubscriber.objects.create(email="a@example.org", confirmed_at=timezone.now())
        NewsletterSubscriber.objects.create(email="b@example.org")
        NewsletterSubscriber.objects.create(email="c@example.org", confirmed_at=timezone.now(), unsubscribed_at=timezone.now())

    def test_list_and_filters(self):
        resp = self.client.get(reverse("studio:subscribers"), {"state": "active"})
        self.assertContains(resp, "a@example.org")
        self.assertNotContains(resp, "b@example.org")

    def test_export_only_active_and_audited(self):
        resp = self.client.get(reverse("studio:subscribers_export"))
        body = resp.content.decode("utf-8-sig")
        self.assertIn("a@example.org", body)
        self.assertNotIn("b@example.org", body)
        self.assertNotIn("c@example.org", body)
        self.assertTrue(AuditEntry.objects.filter(action=Action.SECURITY, message__contains="تصدير").exists())

    def test_delete(self):
        sub = NewsletterSubscriber.objects.get(email="b@example.org")
        self.client.post(reverse("studio:subscriber_delete", args=[sub.pk]))
        self.assertFalse(NewsletterSubscriber.objects.filter(pk=sub.pk).exists())

    def test_reporter_blocked(self):
        login(self.client, make_user("rep", Role.REPORTER))
        self.assertEqual(self.client.get(reverse("studio:subscribers_export")).status_code, 403)


class CalendarTests(ArcmsTestCase):
    def test_week_shows_scheduled_and_published(self):
        chief = make_user("chief", Role.CHIEF)
        cat = make_category()
        make_article("مجدولة غداً", status=Status.SCHEDULED, category=cat, created_by=chief,
                     scheduled_at=timezone.now() + timedelta(hours=1))
        make_article("منشورة اليوم", category=cat, created_by=chief)
        make_article("منشورة قديمة", category=cat, created_by=chief, published_at=timezone.now() - timedelta(days=30))
        login(self.client, chief)
        resp = self.client.get(reverse("studio:calendar"))
        self.assertContains(resp, "مجدولة غداً")
        self.assertContains(resp, "منشورة اليوم")
        self.assertNotContains(resp, "منشورة قديمة")
        old = (timezone.localdate() - timedelta(days=30)).isoformat()
        self.assertContains(self.client.get(reverse("studio:calendar"), {"week": old}), "منشورة قديمة")
        self.assertEqual(self.client.get(reverse("studio:calendar"), {"week": "junk"}).status_code, 200)

    def test_reporter_blocked(self):
        login(self.client, make_user("rep", Role.REPORTER))
        self.assertEqual(self.client.get(reverse("studio:calendar")).status_code, 403)


class ArticleStatsTests(ArcmsTestCase):
    def test_editor_shows_audience_panel_for_published(self):
        chief = make_user("chief", Role.CHIEF)
        article = make_article("منشورة", created_by=chief)
        ua = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Mobile"
        for n in range(3):
            record_view(ip=f"10.0.0.{n}", user_agent=ua, path="/", referrer="https://t.me/x", article_id=article.pk)
        login(self.client, chief)
        resp = self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertContains(resp, "جمهور هذه المادة")
        self.assertEqual(resp.context["stats"]["today"], 3)
        self.assertEqual(resp.context["stats"]["sources"][0][0], "تطبيقات المراسلة")
        draft = make_article("مسودة", status=Status.DRAFT, created_by=chief)
        self.assertIsNone(self.client.get(reverse("studio:article_edit", args=[draft.pk])).context["stats"])


class TemplateKeyCollisionTests(ArcmsTestCase):
    def test_audit_and_pagination_with_items_key(self):
        from arcms.core.models import HomeBlock

        admin = make_user("root", Role.ADMIN)
        login(self.client, admin)
        block = HomeBlock.objects.create(kind="stats")
        block.items = "1 | واحد"
        block.save()  # قيد تدقيق فيه حقل اسمه items
        self.assertEqual(self.client.get(reverse("studio:audit")).status_code, 200)
        self.assertEqual(self.client.get(reverse("studio:audit"), {"items": "x", "page": 1}).status_code, 200)


class SetupWizardTests(ArcmsTestCase):
    def test_wizard_applies_identity_theme_structure(self):
        from arcms.content.models import Category
        from arcms.core.models import HomeBlock, MenuItem, SiteSettings

        login(self.client, make_user("root", Role.ADMIN))
        self.assertContains(self.client.get(reverse("studio:home")), "أكمل إعداد الموقع")
        self.assertContains(self.client.get(reverse("studio:setup")), "معالج إعداد الموقع")
        existing = make_article("مادة قديمة")
        resp = self.client.post(reverse("studio:setup"), {
            "name": "شبكة الغد", "short_name": "الغد", "tagline": "من الميدان", "description": "وصف",
            "telegram": "https://t.me/alghad", "contact_email": "news@alghad.example",
            "theme": "olive-green", "structure": "general",
        })
        self.assertEqual(resp.status_code, 302)
        site = SiteSettings.objects.get(pk=1)
        self.assertEqual((site.name, site.short_name, site.telegram), ("شبكة الغد", "الغد", "https://t.me/alghad"))
        self.assertEqual(site.primary_color, "#1c6b3a")
        self.assertTrue(Category.objects.filter(name="رياضة").exists())
        self.assertTrue(MenuItem.objects.exists())
        self.assertTrue(HomeBlock.objects.exists())
        self.assertTrue(type(existing).objects.filter(pk=existing.pk).exists())  # لا تُحذف المواد
        self.assertIn("شبكة الغد", self.client.get("/").content.decode())

    def test_chief_cannot_open_wizard(self):
        login(self.client, make_user("chief", Role.CHIEF))
        self.assertEqual(self.client.get(reverse("studio:setup")).status_code, 403)


class WhiteLabelTests(ArcmsTestCase):
    def test_client_logo_in_studio_and_login(self):
        from arcms.content.imaging import store_image
        from arcms.core.models import SiteSettings
        from arcms.core.testing import image_bytes

        logo, _ = store_image(image_bytes(fmt="PNG", size=(300, 100)))
        site = SiteSettings.load()
        site.logo = logo
        site.save()
        self.assertContains(self.client.get(reverse("accounts:login")), logo.url)
        login(self.client, make_user("chief", Role.CHIEF))
        self.assertContains(self.client.get(reverse("studio:home")), logo.thumb)
