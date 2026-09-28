from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.utils import timezone

from arcms.content.models import Article, Category, Page, Status
from arcms.core import jobs
from arcms.core.health import run_checks
from arcms.core.models import HomeBlock, Job, MenuItem, SiteSettings, WorkerHeartbeat
from arcms.core.testing import ArcmsTestCase, make_article, make_user


class JobQueueTests(ArcmsTestCase):
    def setUp(self):
        super().setUp()
        self.calls = []
        jobs.handler("test.ok")(lambda p: self.calls.append(p) or {"done": True})

        def boom(p):
            raise RuntimeError("انقطاع مؤقت")

        def fatal(p):
            raise jobs.PermanentError("إعداد خاطئ")

        jobs.handler("test.boom")(boom)
        jobs.handler("test.fatal")(fatal)

    def test_run_and_result(self):
        job = jobs.enqueue("test.ok", {"n": 1})
        self.assertEqual(jobs.run_pending(), 1)
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.DONE)
        self.assertEqual(job.result, {"done": True})
        self.assertEqual(self.calls, [{"n": 1}])

    def test_dedupe_key(self):
        a = jobs.enqueue("test.ok", dedupe_key="daily:1")
        b = jobs.enqueue("test.ok", dedupe_key="daily:1")
        self.assertEqual(a.pk, b.pk)

    def test_retry_with_backoff_then_fail(self):
        job = jobs.enqueue("test.boom", max_attempts=2)
        jobs.run_pending()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.QUEUED)
        self.assertGreater(job.run_after, timezone.now())
        Job.objects.filter(pk=job.pk).update(run_after=timezone.now())
        jobs.run_pending()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertIn("انقطاع مؤقت", job.last_error)

    def test_permanent_error_not_retried(self):
        job = jobs.enqueue("test.fatal", max_attempts=5)
        jobs.run_pending()
        job.refresh_from_db()
        self.assertEqual(job.status, Job.Status.FAILED)
        self.assertEqual(job.attempts, 1)

    def test_future_jobs_wait(self):
        jobs.enqueue("test.ok", run_after=timezone.now() + timedelta(minutes=5))
        self.assertEqual(jobs.run_pending(), 0)

    def test_stale_running_jobs_recovered(self):
        job = jobs.enqueue("test.ok")
        Job.objects.filter(pk=job.pk).update(status=Job.Status.RUNNING, locked_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(jobs.recover_stale(), 1)

    def test_worker_once_publishes_scheduled_and_beats(self):
        chief = make_user("chief", "chief")
        article = make_article("مجدولة", status=Status.SCHEDULED, created_by=chief,
                               scheduled_at=timezone.now() - timedelta(seconds=1))
        with mock.patch("arcms.distribution.dispatch.on_article_published"):
            call_command("arcms_worker", "--once", stdout=StringIO())
        article.refresh_from_db()
        self.assertEqual(article.status, Status.PUBLISHED)
        self.assertTrue(WorkerHeartbeat.healthy())


class SetupCommandTests(ArcmsTestCase):
    def test_setup_builds_site_structure(self):
        call_command("arcms_setup", preset="palestine", site_name="شبكة الاختبار", admin_username="root",
                     admin_password="a-very-long-admin-pass", stdout=StringIO())
        self.assertEqual(SiteSettings.load().name, "شبكة الاختبار")
        self.assertTrue(Category.objects.filter(name="القدس").exists())
        self.assertTrue(MenuItem.objects.filter(location="main", kind="translation").exists())
        self.assertTrue(HomeBlock.objects.filter(kind="hero").exists())
        self.assertTrue(Page.objects.filter(slug="privacy").exists())
        root = __import__("arcms.accounts.models", fromlist=["User"]).User.objects.get(username="root")
        self.assertEqual(root.role, "admin")
        self.assertFalse(root.has_2fa)
        # التشغيل الثاني لا يكرر شيئاً
        call_command("arcms_setup", preset="palestine", stdout=StringIO())
        self.assertEqual(HomeBlock.objects.filter(kind="hero").count(), 1)

    def test_general_preset(self):
        call_command("arcms_setup", preset="general", stdout=StringIO())
        self.assertTrue(Category.objects.filter(name="رياضة").exists())


class HealthTests(ArcmsTestCase):
    def test_dev_settings_flagged(self):
        checks = run_checks()
        by_area = {}
        for c in checks:
            by_area.setdefault(c.area, []).append(c.level)
        self.assertIn("fail", by_area["التشفير"])  # مفتاح الحقول الافتراضي
        self.assertIn("fail", by_area["العامل الخلفي"])
        self.assertIn("ok", by_area["سجل التدقيق"])

    def test_command_exit_code(self):
        with self.assertRaises(SystemExit):
            call_command("arcms_check", stdout=StringIO())


class KeysCommandTests(ArcmsTestCase):
    def test_generates_valid_keys(self):
        out = StringIO()
        call_command("arcms_keys", stdout=out)
        text = out.getvalue()
        from cryptography.fernet import Fernet

        field_key = [ln for ln in text.splitlines() if ln.startswith("ARCMS_FIELD_KEY=")][0].split("=", 1)[1]
        Fernet(field_key.encode())
        self.assertIn("ARCMS_VAPID_PUBLIC_KEY=", text)
        self.assertGreater(len([ln for ln in text.splitlines() if ln.startswith("ARCMS_SECRET_KEY=")][0]), 60)


class SiteSettingsTests(ArcmsTestCase):
    def test_singleton_and_cache_invalidation(self):
        s = SiteSettings.load()
        s.name = "اسم جديد"
        s.save()
        self.assertEqual(SiteSettings.objects.count(), 1)
        self.assertEqual(SiteSettings.load().name, "اسم جديد")

    def test_article_count_unchanged_by_setup_helpers(self):
        self.assertEqual(Article.objects.count(), 0)
