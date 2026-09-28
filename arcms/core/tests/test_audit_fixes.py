"""اختبارات انحدار لنتائج المراجعة الأمنية المستقلة."""

import io
import json
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import call_command
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from arcms.accounts import totp
from arcms.accounts.models import LoginAttempt
from arcms.accounts.roles import Role
from arcms.analytics.models import PageView
from arcms.backups import crypto
from arcms.content import search
from arcms.content.imaging import ImageRejected, strip_and_normalize
from arcms.content.models import Article, BreakingNews, Status, Tag
from arcms.content.workflow import transition
from arcms.core.models import AdSlot, MenuItem
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user
from arcms.core.utils import client_ip, is_safe_link

PASSWORD = "a-long-test-password"


class VisibilityTests(ArcmsTestCase):
    def test_search_hides_future_dated(self):
        make_article("خبر منشور عن الحصار")
        make_article("خبر مؤجل عن الحصار", published_at=timezone.now() + timedelta(days=2))
        call_command("arcms_reindex", stdout=StringIO())
        titles = [a.title for a in search.search_articles("الحصار").articles]
        self.assertEqual(titles, ["خبر منشور عن الحصار"])

    def test_jsonl_import_status_allowlist_and_future(self):
        import tempfile

        rows = [
            {"id": 1, "title": "خاص", "status": "private"},
            {"id": 2, "title": "محذوف", "status": "trash"},
            {"id": 3, "title": "مؤجل", "published_at": (timezone.now() + timedelta(days=3)).isoformat()},
            {"id": 4, "title": "منشور", "status": "publish"},
        ]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "a.jsonl"
            path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            call_command("arcms_import", "jsonl", str(path), stdout=StringIO())
        status = dict(Article.objects.values_list("title", "status"))
        self.assertEqual(status, {"خاص": Status.DRAFT, "محذوف": Status.DRAFT, "مؤجل": Status.SCHEDULED, "منشور": Status.PUBLISHED})

    def test_tag_used_only_on_drafts_is_404(self):
        draft = make_article("مسودة", status=Status.DRAFT)
        tag = Tag.get_or_create_by_name("اغتيال-مرتقب")
        draft.tags.add(tag)
        self.assertEqual(self.client.get(tag.get_absolute_url()).status_code, 404)
        make_article("منشورة").tags.add(tag)
        self.assertEqual(self.client.get(tag.get_absolute_url()).status_code, 200)

    def test_beacon_ignores_draft_ids(self):
        draft = make_article("مسودة سرية", status=Status.DRAFT)
        self.client.post(reverse("public:beacon"), json.dumps({"p": "/", "a": draft.pk}), content_type="application/json")
        self.assertFalse(PageView.objects.filter(article_id=draft.pk).exists())


class BreakingAndLinksTests(ArcmsTestCase):
    def test_safe_link(self):
        for ok in ("/latest/", "https://example.org/x", "http://example.org"):
            self.assertTrue(is_safe_link(ok), ok)
        for bad in ("javascript:alert(1)", " JavaScript:alert(1)", "data:text/html,x", "//evil.example", "/\\evil", "", "vbscript:x"):
            self.assertFalse(is_safe_link(bad), bad)

    def test_breaking_cannot_link_draft_or_script(self):
        social = make_user("social", Role.SOCIAL)
        login(self.client, social)
        draft = make_article("اغتيال قائد", status=Status.DRAFT)
        self.client.post(reverse("studio:breaking"), {"text": "عاجل", "article": draft.pk, "send_telegram": ""})
        self.client.post(reverse("studio:breaking"), {"text": "عاجل", "link": "javascript:alert(1)"})
        self.assertFalse(BreakingNews.objects.exists())
        old = BreakingNews.objects.create(text="قديم", link="javascript:alert(1)")
        self.assertEqual(old.get_url(), "")
        item = MenuItem.objects.create(label="x", link_type=MenuItem.LinkType.URL, url="javascript:alert(1)")
        self.assertEqual(item.get_url(), "#")


class SessionAndScopeTests(ArcmsTestCase):
    def test_preview_requires_completed_2fa(self):
        draft = make_article("مسودة للمعاينة", status=Status.DRAFT)
        editor = make_user("noenroll", Role.EDITOR, with_2fa=False)
        self.client.force_login(editor)  # كلمة المرور فقط، دون تفعيل التحقق الثنائي
        self.assertEqual(self.client.get(draft.get_absolute_url()).status_code, 404)
        chief = make_user("chief", Role.CHIEF)
        c = Client()
        c.force_login(chief)  # جهاز مفعّل لكن الجلسة لم تُدخل الرمز
        self.assertEqual(c.get(draft.get_absolute_url()).status_code, 404)
        login(c, chief)
        self.assertEqual(c.get(draft.get_absolute_url()).status_code, 200)

    def test_desk_scope_applies_to_reading(self):
        politics, sports = make_category("سياسة"), make_category("رياضة")
        editor = make_user("sp", Role.EDITOR)
        editor.desks.set([sports])
        other = make_article("مسودة سياسية", status=Status.DRAFT, category=politics)
        login(self.client, editor)
        self.assertEqual(self.client.get(reverse("studio:article_edit", args=[other.pk])).status_code, 403)
        self.assertEqual(self.client.get(other.get_absolute_url()).status_code, 404)

    def test_author_edit_after_approval_needs_new_review(self):
        cat = make_category()
        head = make_user("head", Role.DESK_HEAD)
        editor = make_user("ed", Role.EDITOR)
        article = make_article("عنوان معتمد", status=Status.DRAFT, category=cat, created_by=head)
        transition(article, head, "submit")
        transition(article, editor, "approve")
        login(self.client, head)
        url = reverse("studio:article_edit", args=[article.pk])
        data = {"kind": "news", "title": "عنوان آخر لم يراجعه أحد", "body": "<p>نص جديد</p>", "category": cat.pk,
                "priority": 0, "action": "publish"}
        resp = self.client.post(url, data, follow=True)
        self.assertContains(resp, "عادت إلى المراجعة")
        article.refresh_from_db()
        self.assertEqual(article.status, Status.IN_REVIEW)
        self.assertIsNone(article.reviewed_by)


class RawHtmlTests(ArcmsTestCase):
    def test_only_admin_sets_ad_code(self):
        chief = make_user("chief", Role.CHIEF)
        login(self.client, chief)
        self.client.post(reverse("studio:ads_new"), {"name": "إعلان", "placement": "sidebar", "is_active": "on",
                                                     "html": "<script>steal()</script>"})
        ad = AdSlot.objects.get()
        self.assertEqual(ad.html, "")
        admin = make_user("root", Role.ADMIN)
        login(self.client, admin)
        self.client.post(reverse("studio:ads_edit", args=[ad.pk]), {"name": "إعلان", "placement": "sidebar",
                                                                    "is_active": "on", "html": "<p>ad</p>"})
        ad.refresh_from_db()
        self.assertEqual(ad.html, "<p>ad</p>")

    def test_staff_sessions_get_strict_csp(self):
        AdSlot.objects.create(name="شبكة", placement="sidebar", html="<script src='https://ads.example/a.js'></script>")
        anon = self.client.get("/")
        self.assertIn("'unsafe-inline' https:", anon["Content-Security-Policy"])
        login(self.client, make_user("rep", Role.REPORTER))
        staff = self.client.get("/")
        self.assertNotIn("https:;", staff["Content-Security-Policy"].split("script-src")[1].split(";")[0] + ";")
        self.assertIn("script-src 'self';", staff["Content-Security-Policy"])


class LoginLockTests(ArcmsTestCase):
    @override_settings(ARCMS_LOGIN_MAX_FAILURES=3)
    def test_attacker_ip_does_not_lock_owner(self):
        make_user("chief", Role.CHIEF)
        for _ in range(4):
            self.client.post(reverse("accounts:login"), {"username": "chief", "password": "x"}, REMOTE_ADDR="198.51.100.66")
        self.assertTrue(LoginAttempt.is_locked("chief", "198.51.100.66"))
        self.assertFalse(LoginAttempt.is_locked("chief", "203.0.113.10"))
        resp = self.client.post(reverse("accounts:login"), {"username": "chief", "password": PASSWORD}, REMOTE_ADDR="203.0.113.10")
        self.assertRedirects(resp, reverse("accounts:verify"), fetch_redirect_response=False)

    @override_settings(ARCMS_LOGIN_MAX_FAILURES=2)
    def test_distributed_guessing_still_locks(self):
        for n in range(20):
            LoginAttempt.objects.create(username="chief", ip=f"198.51.100.{n}", success=False)
        self.assertTrue(LoginAttempt.is_locked("chief", "203.0.113.10"))

    def test_recovery_codes_salted_and_legacy_accepted(self):
        a, b = totp.hash_recovery_code("ABCDE-FGHIJ"), totp.hash_recovery_code("ABCDE-FGHIJ")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("s1$"))
        self.assertTrue(totp.recovery_code_matches("abcde fghij", a))
        self.assertFalse(totp.recovery_code_matches("ABCDE-FGHIK", a))
        import hashlib

        legacy = hashlib.sha256(b"ABCDEFGHIJ").hexdigest()
        user = make_user("legacy")
        user.recovery_codes = [legacy]
        user.save()
        self.assertTrue(user.use_recovery_code("ABCDE-FGHIJ"))
        self.assertEqual(user.recovery_codes, [])


class MiscTests(ArcmsTestCase):
    def test_image_bomb_rejected_before_decoding(self):
        img = Image.new("1", (8000, 6000))  # 48 مليون بكسل في ملف صغير
        buf = io.BytesIO()
        img.save(buf, "PNG")
        with mock.patch("PIL.ImageFile.ImageFile.load", side_effect=AssertionError("decoded")):
            with self.assertRaises(ImageRejected):
                strip_and_normalize(buf.getvalue())

    @override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
    def test_proxy_header_trusted_only_from_proxy(self):
        rf = RequestFactory()
        direct = rf.get("/", REMOTE_ADDR="93.184.216.34", HTTP_X_REAL_IP="10.9.9.9")
        self.assertEqual(client_ip(direct), "93.184.216.34")
        proxied = rf.get("/", REMOTE_ADDR="172.18.0.5", HTTP_X_REAL_IP="203.0.113.44")
        self.assertEqual(client_ip(proxied), "203.0.113.44")

    def test_subscriber_csv_neutralizes_formulas(self):
        from arcms.distribution.models import NewsletterSubscriber

        NewsletterSubscriber.objects.create(email="=HYPERLINK(1)@x.org", confirmed_at=timezone.now(), source="+cmd")
        login(self.client, make_user("social", Role.SOCIAL))
        body = self.client.get(reverse("studio:subscribers_export")).content.decode("utf-8-sig")
        self.assertIn("'=hyperlink", body)
        self.assertIn("'+cmd", body)

    def test_backup_header_cost_bounded(self):
        blob = bytearray(io.BytesIO().getvalue())
        buf = io.BytesIO()
        w = crypto.EncryptingWriter(buf, passphrase="عبارة طويلة", scrypt_log2=10)
        w.write(b"data")
        w.close()
        blob = bytearray(buf.getvalue())
        blob[len(crypto.MAGIC) + 2] = 40  # كلفة تطلب ذاكرة هائلة
        with self.assertRaises(crypto.BackupCryptoError):
            crypto.DecryptingReader(io.BytesIO(bytes(blob)), passphrase="عبارة طويلة").read()
