import io
import os
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from arcms.accounts.roles import Role
from arcms.audit.models import Action, AuditEntry
from arcms.core.models import SiteSettings
from arcms.core.testing import ArcmsTestCase, image_bytes, login, make_user
from arcms.tips import services
from arcms.tips.models import Tip, TipAttachment, TipMessage

BODY = "رأيت شاحنات تُفرغ حمولتها قرب المعبر عند الفجر."


def _jpeg(name="IMG_source.jpg"):
    return SimpleUploadedFile(name, image_bytes(gps=True), content_type="image/jpeg")


class SubmitTests(ArcmsTestCase):
    def _submit(self, **extra):
        data = {"subject": "شحنات", "body": BODY, "contact": "signal: @someone"}
        data.update(extra)
        return self.client.post(reverse("public:tips"), data)

    def test_form_page_is_private(self):
        site = SiteSettings.load()
        site.custom_head_html = '<script src="https://tracker.example/t.js"></script>'
        site.save()
        resp = self.client.get(reverse("public:tips"))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn("tracker.example", html)
        self.assertNotIn("data-track", html)
        self.assertIn("script-src 'self';", resp["Content-Security-Policy"])
        self.assertNotIn("https:", resp["Content-Security-Policy"].split("script-src")[1].split(";")[0])
        self.assertEqual(resp["Cache-Control"], "no-store")

    def test_submission_strips_metadata_and_encrypts(self):
        SiteSettings.load()
        audit_before = AuditEntry.objects.count()
        pdf = SimpleUploadedFile("تقرير-سري.pdf", b"%PDF-1.4\n/Author (Ahmad)\n%%EOF", content_type="application/pdf")
        resp = self._submit(files=[_jpeg(), pdf])
        self.assertContains(resp, "رمزك السري")
        code = resp.context["code"]
        tip = Tip.objects.get()
        self.assertEqual(tip.code_hash, services.hash_code(code))
        self.assertEqual(tip.body, BODY)
        self.assertTrue(tip.unread)
        # لا شيء من المرسل في القاعدة إلا ما كتبه: لا عنوان ولا متصفح ولا اسم ملف
        self.assertEqual(AuditEntry.objects.count(), audit_before)
        field_names = {f.name for m in (Tip, TipAttachment) for f in m._meta.fields}
        self.assertFalse({"ip", "user_agent", "filename", "name"} & field_names)
        image = tip.attachments.get(kind="image")
        self.assertNotIn(b"PhoneMaker", bytes(image.ciphertext))
        self.assertNotIn(b"\xff\xd8", bytes(image.ciphertext)[:4])
        clean = Image.open(io.BytesIO(services.read_attachment(image)))
        self.assertFalse(clean.getexif())
        self.assertTrue(image.removed)
        doc = tip.attachments.get(kind="pdf")
        self.assertTrue(doc.metadata_warning)
        self.assertNotIn(b"Ahmad", bytes(doc.ciphertext))
        self.assertIn(b"Ahmad", services.read_attachment(doc))
        # النص مشفّر في القاعدة
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT body FROM tips_tip")
            raw = cur.fetchone()[0]
        self.assertTrue(raw.startswith("enc1:"))

    def test_large_files_never_touch_disk(self):
        big = b"%PDF-1.7\n" + os.urandom(3 * 1024 * 1024)  # أكبر من حد الذاكرة الافتراضي في Django
        with mock.patch("django.core.files.uploadhandler.TemporaryUploadedFile", side_effect=AssertionError("disk")):
            resp = self._submit(files=[SimpleUploadedFile("big.pdf", big, content_type="application/pdf")])
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(services.read_attachment(TipAttachment.objects.get()), big)

    def test_rejects_other_file_types_and_short_body(self):
        resp = self._submit(files=[SimpleUploadedFile("x.exe", b"MZ\x90\x00binary", content_type="application/octet-stream")])
        self.assertContains(resp, "نقبل الصور وملفات PDF فقط", status_code=400)
        resp = self._submit(body="قصير")
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(Tip.objects.exists())

    def test_honeypot_pretends_success(self):
        resp = self._submit(website="http://spam")
        self.assertContains(resp, "رمزك السري")
        self.assertFalse(Tip.objects.exists())

    @mock.patch.dict("arcms.tips.views.LIMITS", {"submit": (2, 3600), "follow": (30, 3600)})
    def test_rate_limited(self):
        for _ in range(3):
            resp = self._submit()
        self.assertEqual(resp.status_code, 429)
        self.assertEqual(Tip.objects.count(), 2)

    def test_disabled(self):
        site = SiteSettings.load()
        site.tips_enabled = False
        site.save()
        self.assertEqual(self.client.get(reverse("public:tips")).status_code, 404)


class FollowTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.tip, self.code = services.create_tip(body=BODY, subject="شحنات")

    def test_wrong_code(self):
        resp = self.client.post(reverse("public:tips_follow"), {"code": "AAAAA-BBBBB-CCCCC-DDDDD"})
        self.assertContains(resp, "الرمز غير صحيح", status_code=400)

    def test_code_shows_replies_but_not_own_text(self):
        chief = make_user("chief", Role.CHIEF)
        services.add_newsroom_reply(self.tip, chief, "هل لديك صورة للشاحنات؟")
        lowered = self.code.lower().replace("-", " ")
        resp = self.client.post(reverse("public:tips_follow"), {"code": lowered})
        self.assertContains(resp, "هل لديك صورة للشاحنات؟")
        self.assertNotContains(resp, BODY)
        self.assertNotContains(resp, "chief")

    def test_source_reply_with_image(self):
        Tip.objects.filter(pk=self.tip.pk).update(unread=False, status=Tip.Status.CLOSED)
        resp = self.client.post(reverse("public:tips_follow"), {"code": self.code, "body": "نعم، هذه صورة.", "files": [_jpeg()]})
        self.assertContains(resp, "وصلت رسالتك الجديدة")
        self.tip.refresh_from_db()
        self.assertTrue(self.tip.unread)
        self.assertEqual(self.tip.status, Tip.Status.REVIEWING)
        msg = TipMessage.objects.get(tip=self.tip)
        self.assertEqual(msg.attachments.count(), 1)

    @mock.patch.dict("arcms.tips.views.LIMITS", {"submit": (6, 3600), "follow": (3, 3600)})
    def test_guessing_is_rate_limited(self):
        for _ in range(3):
            self.client.post(reverse("public:tips_follow"), {"code": "AAAAA-BBBBB-CCCCC-DDDDD"})
        resp = self.client.post(reverse("public:tips_follow"), {"code": self.code})
        self.assertContains(resp, "محاولات كثيرة", status_code=429)
        self.assertNotIn("tip", resp.context)

    def test_codes_are_unique_and_long(self):
        codes = {services.new_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)
        self.assertTrue(all(len(services.normalize_code(c)) == services.CODE_LENGTH for c in codes))
        self.assertIsNone(services.find_tip(self.code[:-1]))


class StudioTipsTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.tip, _ = services.create_tip(
            body=BODY, subject="شحنات", files=[image_bytes(gps=True), b"%PDF-1.4 doc"]
        )
        self.chief = make_user("chief", Role.CHIEF)

    def test_only_chief_and_admin(self):
        for role in (Role.REPORTER, Role.EDITOR, Role.DESK_HEAD, Role.SOCIAL):
            login(self.client, make_user(f"u-{role}", role))
            self.assertEqual(self.client.get(reverse("studio:tips")).status_code, 403)
            self.assertEqual(self.client.get(reverse("studio:tip_detail", args=[self.tip.pk])).status_code, 403)
        login(self.client, make_user("admin", Role.ADMIN))
        self.assertEqual(self.client.get(reverse("studio:tips")).status_code, 200)

    def test_detail_access_is_audited_once(self):
        login(self.client, self.chief)
        resp = self.client.get(reverse("studio:tips"))
        self.assertContains(resp, self.tip.ref)
        for _ in range(2):
            resp = self.client.get(reverse("studio:tip_detail", args=[self.tip.pk]))
        self.assertContains(resp, BODY)
        self.assertEqual(AuditEntry.objects.filter(action=Action.SECURITY, message__contains="اطلاع على البلاغ").count(), 1)
        self.tip.refresh_from_db()
        self.assertFalse(self.tip.unread)

    def test_attachment_download(self):
        login(self.client, self.chief)
        image = self.tip.attachments.get(kind="image")
        resp = self.client.get(reverse("studio:tip_attachment", args=[self.tip.pk, image.pk]))
        self.assertEqual(resp["Content-Type"], "image/jpeg")
        self.assertIn("attachment;", resp["Content-Disposition"])
        self.assertIn("sandbox", resp["Content-Security-Policy"])
        self.assertTrue(AuditEntry.objects.filter(message__contains="تنزيل مرفق").exists())
        resp = self.client.get(reverse("studio:tip_attachment", args=[self.tip.pk, image.pk]) + "?inline=1")
        self.assertIn("inline;", resp["Content-Disposition"])
        doc = self.tip.attachments.get(kind="pdf")
        resp = self.client.get(reverse("studio:tip_attachment", args=[self.tip.pk, doc.pk]) + "?inline=1")
        self.assertIn("attachment;", resp["Content-Disposition"])
        other, _ = services.create_tip(body=BODY)
        self.assertEqual(self.client.get(reverse("studio:tip_attachment", args=[other.pk, image.pk])).status_code, 404)

    def test_reply_update_delete(self):
        login(self.client, self.chief)
        url = reverse("studio:tip_detail", args=[self.tip.pk])
        self.client.post(url, {"action": "reply", "body": "شكراً، هل يمكنك التوضيح؟"})
        self.assertTrue(TipMessage.objects.filter(tip=self.tip, from_source=False, author=self.chief).exists())
        self.client.post(url, {"action": "update", "status": "used", "assigned_to": self.chief.pk, "note": "مصدر موثوق"})
        self.tip.refresh_from_db()
        self.assertEqual((self.tip.status, self.tip.assigned_to, self.tip.note), ("used", self.chief, "مصدر موثوق"))
        reporter = make_user("rep", Role.REPORTER)
        self.client.post(url, {"action": "update", "status": "used", "assigned_to": reporter.pk})
        self.tip.refresh_from_db()
        self.assertIsNone(self.tip.assigned_to)  # لا يُسند البلاغ لمن لا يملك الصلاحية
        self.client.post(reverse("studio:tip_delete", args=[self.tip.pk]))
        self.assertFalse(Tip.objects.exists())
        self.assertFalse(TipAttachment.objects.exists())

    def test_retention_purge(self):
        old_closed, _ = services.create_tip(body=BODY)
        Tip.objects.filter(pk=old_closed.pk).update(status=Tip.Status.CLOSED, updated_at=timezone.now() - timedelta(days=200))
        old_open, _ = services.create_tip(body=BODY)
        Tip.objects.filter(pk=old_open.pk).update(updated_at=timezone.now() - timedelta(days=200))
        from arcms.tips.tasks import tips_purge

        tips_purge()
        self.assertFalse(Tip.objects.filter(pk=old_closed.pk).exists())
        self.assertTrue(Tip.objects.filter(pk=old_open.pk).exists())
        self.assertTrue(AuditEntry.objects.filter(message__contains="حذف تلقائي").exists())


@override_settings(ARCMS_TIPS_HOST="tips.news.example", ARCMS_TIPS_ORIGIN="https://tips.news.example",
                   ALLOWED_HOSTS=["testserver", "tips.news.example"])
class TipsHostTests(ArcmsTestCase):
    def test_main_host_redirects_to_tips_host(self):
        resp = self.client.get(reverse("public:tips_follow"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "https://tips.news.example/tips/follow/")
        self.assertIn('href="https://tips.news.example/tips/"', self.client.get("/").content.decode())

    def test_tips_host_serves_only_tips(self):
        host = {"HTTP_HOST": "tips.news.example"}
        self.assertEqual(self.client.get("/", **host)["Location"], "/tips/")
        resp = self.client.get(reverse("public:tips"), **host)
        self.assertContains(resp, "أرسل معلومة بأمان")
        self.assertNotContains(resp, "public.js")
        for path in ("/studio/", "/accounts/login/", "/latest/", "/search?q=x", "/media/x.jpg"):
            self.assertEqual(self.client.get(path, **host).status_code, 404, path)
        resp = self.client.post(reverse("public:tips"), {"body": BODY}, **host)
        self.assertContains(resp, "رمزك السري")
