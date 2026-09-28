"""المهام الدورية العامة: النشر المجدول، انتهاء العاجل، الأقفال، المهام العالقة."""

from datetime import timedelta

from django.utils import timezone

from .jobs import periodic, recover_stale


@periodic("publish_due", 15)
def publish_due_articles():
    from arcms.content.workflow import publish_due

    publish_due()


@periodic("housekeeping", 300)
def housekeeping():
    from arcms.accounts.models import LoginAttempt
    from arcms.content.models import ArticleLock

    from .models import Job

    recover_stale()
    ArticleLock.objects.filter(heartbeat_at__lt=timezone.now() - timedelta(minutes=10)).delete()
    LoginAttempt.objects.filter(created_at__lt=timezone.now() - timedelta(days=90)).delete()
    Job.objects.filter(status=Job.Status.DONE, finished_at__lt=timezone.now() - timedelta(days=14)).delete()
