from django.core import mail
from django.urls import reverse

from arcms.accounts.roles import Role
from arcms.content.models import Status
from arcms.content.workflow import publish_due, transition
from arcms.core.jobs import run_pending
from arcms.core.testing import ArcmsTestCase, login, make_article, make_category, make_user


def _user(name, role, **extra):
    user = make_user(name, role)
    user.email = f"{name}@newsroom.example"
    for k, v in extra.items():
        setattr(user, k, v)
    user.save()
    return user


class WorkflowNotificationTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.politics = make_category("سياسة")
        self.sports = make_category("رياضة")
        self.reporter = _user("rep", Role.REPORTER)
        self.editor = _user("ed", Role.EDITOR)
        self.sports_editor = _user("sp", Role.EDITOR)
        self.sports_editor.desks.set([self.sports])
        self.chief = _user("chief", Role.CHIEF)
        self.article = make_article("تحقيق عن صفقة سرية", status=Status.DRAFT, category=self.politics, created_by=self.reporter)

    def _do(self, user, action, **kw):
        with self.captureOnCommitCallbacks(execute=True):
            transition(self.article, user, action, **kw)
        mail.outbox.clear()
        run_pending()
        return {m.to[0] for m in mail.outbox}

    def test_cycle(self):
        to = self._do(self.reporter, "submit")
        self.assertEqual(to, {"ed@newsroom.example", "chief@newsroom.example"})  # لا محرر الرياضة ولا الكاتب
        self.assertNotIn("صفقة سرية", mail.outbox[0].subject + mail.outbox[0].body)
        self.assertIn(f"/studio/articles/{self.article.pk}/", mail.outbox[0].body)

        to = self._do(self.editor, "return", note="أضف مصدراً ثانياً")
        self.assertEqual(to, {"rep@newsroom.example"})
        self.assertNotIn("مصدراً ثانياً", mail.outbox[0].body)

        self._do(self.reporter, "submit")
        to = self._do(self.editor, "approve")
        self.assertIn("chief@newsroom.example", to)
        self.assertIn("rep@newsroom.example", to)
        self.assertNotIn("ed@newsroom.example", to)

        to = self._do(self.chief, "publish")
        self.assertEqual(to, {"rep@newsroom.example"})
        self.assertIn("صفقة سرية", mail.outbox[0].subject)  # بعد النشر فقط

    def test_opt_out(self):
        login(self.client, self.editor)
        self.client.post(reverse("studio:profile"), {"action": "notifications"})
        self.editor.refresh_from_db()
        self.assertFalse(self.editor.email_notifications)
        to = self._do(self.reporter, "submit")
        self.assertEqual(to, {"chief@newsroom.example"})

    def test_scheduled_publish_notifies_author(self):
        from datetime import timedelta

        from django.utils import timezone

        self.article.status = Status.SCHEDULED
        self.article.scheduled_at = timezone.now() - timedelta(minutes=1)
        self.article.published_by = self.chief
        self.article.save()
        with self.captureOnCommitCallbacks(execute=True):
            publish_due()
        run_pending()
        self.assertIn("rep@newsroom.example", {m.to[0] for m in mail.outbox})


class TipNotificationTests(ArcmsTestCase):
    def test_new_tip_mail_has_no_content(self):
        _user("chief", Role.CHIEF)
        _user("ed", Role.EDITOR)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("public:tips"), {"body": "معلومة حساسة عن مكان الاحتجاز"})
        run_pending()
        self.assertEqual([m.to[0] for m in mail.outbox], ["chief@newsroom.example"])
        self.assertNotIn("الاحتجاز", mail.outbox[0].body)
