import json
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content.models import Article, ArticleLock, Category, LiveCoverage, MediaAsset, Status
from arcms.core.models import HomeBlock
from arcms.core.testing import ArcmsTestCase, image_bytes, login, make_article, make_category, make_user


class StudioAccessTests(ArcmsTestCase):
    """كل صفحات غرفة التحرير تعمل لمدير النظام، والرتب الأدنى تُمنع مما لا يخصها."""

    def setUp(self):
        super().setUp()
        call_command("arcms_setup", preset="palestine", stdout=StringIO())
        self.admin = make_user("admin", Role.ADMIN)
        self.article = make_article("مادة", category=Category.objects.first(), created_by=self.admin)
        self.live = LiveCoverage.objects.create(title="تغطية")

    def test_every_page_renders_for_admin(self):
        login(self.client, self.admin)
        names = [
            "home", "articles", "article_new", "media", "breaking", "live_list", "homepage", "homepage_new", "settings",
            "users", "user_new", "profile", "distribution", "whatsapp_subscribers", "analytics", "audit", "backups",
            "importer", "inbox", "health", "categories_list", "categories_new", "tags_list", "authors_list",
            "authors_new", "dossiers_list", "pages_list", "pages_new", "menus_list", "menus_new", "ads_list", "ads_new",
        ]
        for name in names:
            resp = self.client.get(reverse(f"studio:{name}"))
            self.assertEqual(resp.status_code, 200, name)
        for url in (
            reverse("studio:article_edit", args=[self.article.pk]),
            reverse("studio:live_detail", args=[self.live.pk]),
            reverse("studio:channel_edit", args=["telegram"]),
            reverse("studio:homepage_edit", args=[HomeBlock.objects.first().pk]),
            reverse("studio:categories_edit", args=[Category.objects.first().pk]),
            reverse("studio:api_tags") + "?q=ق",
            reverse("studio:api_articles") + "?q=مادة",
            reverse("studio:media_picker"),
        ):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_reporter_limits(self):
        reporter = make_user("rep", Role.REPORTER)
        login(self.client, reporter)
        for name in ("settings", "users", "audit", "homepage", "analytics", "backups", "categories_list", "breaking"):
            self.assertEqual(self.client.get(reverse(f"studio:{name}")).status_code, 403, name)
        for name in ("home", "articles", "article_new", "media", "live_list"):
            self.assertEqual(self.client.get(reverse(f"studio:{name}")).status_code, 200, name)
        # لا يرى مواد غيره
        self.assertEqual(self.client.get(reverse("studio:article_edit", args=[self.article.pk])).status_code, 403)

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("studio:home"))
        self.assertTrue(resp["Location"].startswith(reverse("accounts:login")))
        self.assertEqual(resp["Referrer-Policy"], "same-origin")


class EditorTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.cat = make_category("غزة")
        self.reporter = make_user("rep", Role.REPORTER)
        self.editor = make_user("ed", Role.EDITOR)

    def post_article(self, pk=None, **overrides):
        data = {
            "kind": "news", "title": "خبر من المحرر", "subtitle": "", "excerpt": "", "dateline": "", "kicker": "",
            "body": '<p>متن <script>x()</script></p>', "category": self.cat.pk, "tag_names": "غزة، الأسرى",
            "priority": 0, "send_telegram": "on", "in_newsletter": "on", "allow_indexing": "on",
            "action": "save",
        }
        data.update(overrides)
        url = reverse("studio:article_edit", args=[pk]) if pk else reverse("studio:article_new")
        return self.client.post(url, data)

    def test_create_submit_and_sanitize(self):
        login(self.client, self.reporter)
        resp = self.post_article()
        article = Article.objects.get()
        self.assertRedirects(resp, reverse("studio:article_edit", args=[article.pk]), fetch_redirect_response=False)
        self.assertEqual(article.created_by, self.reporter)
        self.assertNotIn("script", article.body)
        self.assertEqual(sorted(article.tags.values_list("name", flat=True)), ["الأسرى", "غزة"])
        self.assertEqual(article.revisions.count(), 1)
        self.post_article(article.pk, action="submit", note="جاهزة للمراجعة")
        article.refresh_from_db()
        self.assertEqual(article.status, Status.IN_REVIEW)
        # محاولة النشر من مراسل تُرفض برسالة ولا تغيّر الحالة
        resp = self.post_article(article.pk, action="publish")
        article.refresh_from_db()
        self.assertEqual(article.status, Status.IN_REVIEW)

    def test_edit_lock(self):
        article = make_article("مقفلة", status=Status.DRAFT, category=self.cat, created_by=self.editor)
        other = make_user("ed2", Role.EDITOR)
        login(self.client, self.editor)
        self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertEqual(ArticleLock.objects.get(article=article).user, self.editor)
        login(self.client, other)
        resp = self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertContains(resp, "يحرّر")
        self.assertFalse(resp.context["editable"])

    def test_autosave_only_for_drafts(self):
        article = make_article("مسودة", status=Status.DRAFT, category=self.cat, created_by=self.reporter)
        login(self.client, self.reporter)
        self.client.get(reverse("studio:article_edit", args=[article.pk]))
        resp = self.client.post(reverse("studio:article_autosave", args=[article.pk]),
                                data=json.dumps({"title": "عنوان محفوظ تلقائياً", "body": "<p>نص</p>"}),
                                content_type="application/json")
        self.assertTrue(resp.json()["ok"])
        article.refresh_from_db()
        self.assertEqual(article.title, "عنوان محفوظ تلقائياً")
        published = make_article("منشورة", category=self.cat, created_by=self.reporter)
        resp = self.client.post(reverse("studio:article_autosave", args=[published.pk]), data="{}",
                                content_type="application/json")
        self.assertFalse(resp.json()["ok"])

    def test_source_notes_hidden_from_other_editors(self):
        article = make_article("سرية", status=Status.DRAFT, category=self.cat, created_by=self.reporter,
                               source_notes="هوية-الشاهد-7731")
        login(self.client, self.editor)
        resp = self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertNotContains(resp, "هوية-الشاهد-7731")
        self.assertContains(resp, "لا تملك صلاحية الاطلاع")
        chief = make_user("chief", Role.CHIEF)
        login(self.client, chief)
        self.assertContains(self.client.get(reverse("studio:article_edit", args=[article.pk])), "هوية-الشاهد-7731")

    def test_revision_restore(self):
        article = make_article("الأصل", status=Status.DRAFT, category=self.cat, created_by=self.reporter)
        from arcms.content.models import ArticleRevision

        first = ArticleRevision.capture(article, self.reporter)
        article.title = "المعدّل"
        article.save()
        ArticleRevision.capture(article, self.reporter)
        login(self.client, self.reporter)
        resp = self.client.get(reverse("studio:article_revision", args=[article.pk, first.pk]))
        self.assertContains(resp, "<del>الأصل</del>")
        self.client.post(reverse("studio:article_revision", args=[article.pk, first.pk]))
        article.refresh_from_db()
        self.assertEqual(article.title, "الأصل")


class MediaUploadTests(ArcmsTestCase):
    def test_upload_strips_gps_via_endpoint(self):
        user = make_user("photo", Role.REPORTER)
        login(self.client, user)
        upload = SimpleUploadedFile("IMG_Source_Name_GPS.jpg", image_bytes(gps=True), content_type="image/jpeg")
        resp = self.client.post(reverse("studio:media_upload"), {"file": upload, "json": "1", "credit": "خاص"},
                                HTTP_ACCEPT="application/json")
        data = resp.json()
        self.assertEqual(len(data["items"]), 1)
        self.assertIn("إحداثيات الموقع الجغرافي (GPS)", data["items"][0]["removed"])
        asset = MediaAsset.objects.get()
        self.assertNotIn("Source_Name", asset.file.name)
        self.assertEqual(asset.credit, "خاص")

    def test_upload_rejects_svg(self):
        user = make_user("photo2", Role.REPORTER)
        login(self.client, user)
        upload = SimpleUploadedFile("x.svg", b"<svg onload='alert(1)'/>", content_type="image/svg+xml")
        resp = self.client.post(reverse("studio:media_upload"), {"file": upload, "json": "1"}, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(MediaAsset.objects.exists())


class SiteBuilderTests(ArcmsTestCase):
    def test_homepage_block_reorder_and_toggle(self):
        chief = make_user("chief", Role.CHIEF)
        login(self.client, chief)
        a = HomeBlock.objects.create(kind="latest", order=10)
        b = HomeBlock.objects.create(kind="most_read", order=20)
        self.client.post(reverse("studio:homepage_action", args=[b.pk]), {"action": "up"})
        self.assertEqual(list(HomeBlock.objects.values_list("pk", flat=True)), [b.pk, a.pk])
        self.client.post(reverse("studio:homepage_action", args=[a.pk]), {"action": "toggle"})
        a.refresh_from_db()
        self.assertFalse(a.is_active)

    def test_create_category_and_menu(self):
        chief = make_user("chief2", Role.CHIEF)
        login(self.client, chief)
        self.client.post(reverse("studio:categories_new"), {"name": "رياضة", "slug": "", "order": 5, "is_active": "on"})
        cat = Category.objects.get(name="رياضة")
        self.assertEqual(cat.slug, "رياضة")
        self.client.post(reverse("studio:menus_new"), {"location": "main", "label": "رياضة", "link_type": "category",
                                                     "category": cat.pk, "order": 1, "is_active": "on"})
        resp = self.client.get("/")
        self.assertContains(resp, cat.get_absolute_url())

    def test_settings_change_theme(self):
        admin = make_user("adm", Role.ADMIN)
        login(self.client, admin)
        resp = self.client.get(reverse("studio:settings"))
        form = resp.context["form"]
        data = {k: v for k, v in form.initial.items() if v is not None and not isinstance(v, bool)}
        data.update({k: "on" for k, v in form.initial.items() if v is True})
        data.update({"name": "منبر", "primary_color": "#0f5e8c", "month_style": "egypt"})
        resp = self.client.post(reverse("studio:settings"), data)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", {}) and resp.context["form"].errors)
        home = self.client.get("/")
        self.assertContains(home, "--primary:#0f5e8c")
        self.assertContains(home, "منبر")
