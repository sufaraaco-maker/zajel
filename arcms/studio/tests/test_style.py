import json

from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, login, make_article, make_user


class StyleApiTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.reporter = make_user("rep", Role.REPORTER)

    def check(self, fields, user=None):
        login(self.client, user or self.reporter)
        return self.client.post(reverse("studio:api_style"), json.dumps({"fields": fields}),
                                content_type="application/json")

    def test_returns_issues_per_field_with_house_rules(self):
        site = SiteSettings.load()
        site.style_rules = "مسئول* => مسؤول | نكتب الهمزة على الواو"
        site.style_disabled = ["quotes"]
        site.save()
        resp = self.check({"title": 'قال المسئولون "لا"', "body": "في في الصباح , غادر\n"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        title, body = data["fields"]["title"], data["fields"]["body"]
        self.assertEqual([(i["rule"], i["fix"], i["message"]) for i in title],
                         [("house", "المسؤولون", "نكتب الهمزة على الواو")])  # التنصيص معطّل
        self.assertEqual([i["rule"] for i in body], ["repeat", "punctuation"])
        self.assertEqual(data["total"], 3)

    def test_permissions_and_bad_input(self):
        social = make_user("soc", Role.SOCIAL)
        self.assertEqual(self.check({"title": "نص"}, user=social).status_code, 403)
        self.client.logout()
        resp = self.client.post(reverse("studio:api_style"), "{}", content_type="application/json")
        self.assertEqual(resp.status_code, 302)
        for bad in ({"title": 5}, ["x"], {f"f{i}": "" for i in range(20)}):
            self.assertEqual(self.check(bad).status_code, 400, bad)
        self.assertEqual(self.check({"body": "كلمة " * 40_000}).status_code, 413)
        self.assertEqual(self.client.get(reverse("studio:api_style")).status_code, 405)


class StyleGuidePageTests(ArcmsTestCase):
    def test_only_chief_and_admin(self):
        login(self.client, make_user("ed", Role.DESK_HEAD))
        self.assertEqual(self.client.get(reverse("studio:style")).status_code, 403)
        login(self.client, make_user("boss", Role.CHIEF))
        resp = self.client.get(reverse("studio:style"))
        self.assertContains(resp, "دليل الأسلوب")
        self.assertContains(resp, 'data-style-field="sample"')

    def test_save_rules_and_checks(self):
        login(self.client, make_user("boss", Role.CHIEF))
        resp = self.client.post(reverse("studio:style"), {
            "style_rules": "مسئول => مسؤول\r\nكلمة => كلمة\r\n", "checks": ["punctuation", "hamza"],
        }, follow=True)
        self.assertContains(resp, "بعض الأسطر أُهملت")
        site = SiteSettings.load()
        self.assertEqual(site.style_rules, "مسئول => مسؤول\nكلمة => كلمة")
        self.assertEqual(set(site.style_disabled), {"spacing", "typos", "repeat", "tatweel", "quotes"})
        self.assertContains(self.client.get(reverse("studio:style")), "(1 قاعدة)")

    def test_general_settings_save_keeps_style_guide(self):
        site = SiteSettings.load()
        site.style_rules = "تم | تجنبها"
        site.style_disabled = ["quotes"]
        site.save()
        login(self.client, make_user("adm", Role.ADMIN))
        form = self.client.get(reverse("studio:settings")).context["form"]
        self.assertNotIn("style_rules", form.fields)
        data = {k: v for k, v in form.initial.items() if v is not None and not isinstance(v, bool)}
        data.update({k: "on" for k, v in form.initial.items() if v is True})
        self.assertEqual(self.client.post(reverse("studio:settings"), data).status_code, 302)
        site = SiteSettings.objects.get(pk=1)
        self.assertEqual((site.style_rules, site.style_disabled), ("تم | تجنبها", ["quotes"]))


class EditorPanelTests(ArcmsTestCase):
    def test_article_editor_has_panel(self):
        user = make_user("rep", Role.REPORTER)
        article = make_article(created_by=user)
        login(self.client, user)
        resp = self.client.get(reverse("studio:article_edit", args=[article.pk]))
        self.assertContains(resp, "data-style-panel")
        self.assertContains(resp, '["title","id_title","العنوان"]')
        self.assertNotContains(resp, reverse("studio:style"))  # الرابط للدليل لمن يديره فقط
