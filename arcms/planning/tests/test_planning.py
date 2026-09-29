from datetime import timedelta

from django.core import mail
from django.urls import reverse
from django.utils import timezone

from arcms.accounts.roles import Role
from arcms.audit.models import AuditEntry
from arcms.content.models import Article, EditorialNote, Status
from arcms.content.workflow import transition
from arcms.core.jobs import run_pending
from arcms.core.testing import ArcmsTestCase, login, make_category, make_user
from arcms.planning.models import Assignment


class PlanningTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.desk = make_user("deskhead", Role.DESK_HEAD)
        self.reporter = make_user("rep", Role.REPORTER)
        self.reporter.email = "rep@example.org"
        self.reporter.save()
        self.other = make_user("rep2", Role.REPORTER)
        self.cat = make_category("محليات")

    def create(self, **extra):
        login(self.client, self.desk)
        data = {"title": "تغطية اجتماع المجلس البلدي", "brief": "اسأل عن موازنة الطرق. المصدر: عضو المجلس.",
                "category": self.cat.pk, "kind": "news", "assignee": self.reporter.pk, "priority": 1,
                "status": "idea", "due_at": (timezone.localtime() + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M")}
        data.update(extra)
        resp = self.client.post(reverse("studio:assignment_new"), data)
        self.assertEqual(resp.status_code, 302, getattr(resp, "context", None) and resp.context["form"].errors)
        return Assignment.objects.latest("pk")

    def test_assign_notifies_without_title_and_encrypts_brief(self):
        a = self.create()
        self.assertEqual(a.status, Assignment.Status.ASSIGNED)  # مكلَّف ← لا تبقى «فكرة»
        self.assertEqual(a.created_by, self.desk)
        run_pending()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["rep@example.org"])
        self.assertNotIn("المجلس البلدي", mail.outbox[0].subject + mail.outbox[0].body)
        from django.db import connection

        with connection.cursor() as cur:
            cur.execute("SELECT brief FROM planning_assignment WHERE id = %s", [a.pk])
            self.assertNotIn("موازنة", cur.fetchone()[0])
        self.assertTrue(AuditEntry.objects.filter(object_type="planning.assignment").exists())

    def test_reporter_sees_only_own_and_starts_article(self):
        mine = self.create()
        theirs = self.create(title="مهمة زميل", assignee=self.other.pk)
        login(self.client, self.reporter)
        board = self.client.get(reverse("studio:planning"))
        self.assertContains(board, mine.title)
        self.assertNotContains(board, theirs.title)
        self.assertContains(self.client.get(reverse("studio:home")), "مهامي")
        # لا يبدأ مهمة زميله
        self.assertEqual(self.client.post(reverse("studio:assignment_start", args=[theirs.pk])).status_code, 404)
        resp = self.client.post(reverse("studio:assignment_start", args=[mine.pk]))
        article = Article.objects.get(title=mine.title)
        self.assertRedirects(resp, reverse("studio:article_edit", args=[article.pk]), fetch_redirect_response=False)
        self.assertEqual((article.created_by, article.category, article.status), (self.reporter, self.cat, Status.DRAFT))
        self.assertTrue(EditorialNote.objects.filter(article=article, body__contains="موازنة الطرق").exists())
        mine.refresh_from_db()
        self.assertEqual((mine.article, mine.status), (article, Assignment.Status.WORKING))
        # البدء مرة أخرى يفتح المادة نفسها
        self.client.post(reverse("studio:assignment_start", args=[mine.pk]))
        self.assertEqual(Article.objects.filter(title=mine.title).count(), 1)

    def test_stage_follows_article_workflow(self):
        a = self.create()
        login(self.client, self.reporter)
        self.client.post(reverse("studio:assignment_start", args=[a.pk]))
        article = Article.objects.get(title=a.title)
        article.body = "<p>نص المادة</p>"
        article.save()
        transition(article, self.reporter, "submit")
        a.refresh_from_db()
        self.assertEqual(a.status, Assignment.Status.FILED)
        chief = make_user("chief", Role.CHIEF)
        transition(article, chief, "approve")
        transition(article, chief, "publish")
        a.refresh_from_db()
        self.assertEqual(a.status, Assignment.Status.DONE)

    def test_permissions_and_drop(self):
        login(self.client, self.reporter)
        self.assertEqual(self.client.get(reverse("studio:assignment_new")).status_code, 403)
        a = self.create()
        login(self.client, make_user("social", Role.SOCIAL))
        self.assertEqual(self.client.get(reverse("studio:planning")).status_code, 403)
        login(self.client, self.desk)
        self.client.post(reverse("studio:assignment_action", args=[a.pk]), {"action": "drop"})
        a.refresh_from_db()
        self.assertEqual(a.status, Assignment.Status.DROPPED)
        self.assertNotContains(self.client.get(reverse("studio:planning")), a.title)
        self.assertContains(self.client.get(reverse("studio:planning"), {"dropped": "1"}), a.title)
        self.client.post(reverse("studio:assignment_action", args=[a.pk]), {"action": "restore"})
        a.refresh_from_db()
        self.assertEqual(a.status, Assignment.Status.ASSIGNED)

    def test_assigned_status_needs_assignee_and_overdue_flag(self):
        login(self.client, self.desk)
        resp = self.client.post(reverse("studio:assignment_new"), {"title": "بلا مكلف", "kind": "news", "priority": 0,
                                                                      "status": "assigned"})
        self.assertContains(resp, "اختر من تكلّفه")
        a = Assignment.objects.create(title="متأخرة", assignee=self.reporter, status=Assignment.Status.ASSIGNED,
                                      due_at=timezone.now() - timedelta(hours=1))
        self.assertTrue(a.is_overdue)
        login(self.client, self.reporter)
        self.assertContains(self.client.get(reverse("studio:planning")), "متأخرة")
        self.assertContains(self.client.get(reverse("studio:home")), 'title="مهام بانتظارك"')

    def test_desk_scoping(self):
        other_cat = make_category("رياضة")
        self.cat.desk_members.add(self.desk)
        outside = Assignment.objects.create(title="مهمة قسم آخر", category=other_cat)
        inside = Assignment.objects.create(title="مهمة قسمي", category=self.cat)
        login(self.client, self.desk)
        board = self.client.get(reverse("studio:planning"))
        self.assertContains(board, inside.title)
        self.assertNotContains(board, outside.title)
        self.assertEqual(self.client.get(reverse("studio:assignment_edit", args=[outside.pk])).status_code, 404)
