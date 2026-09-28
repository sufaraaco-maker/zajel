from django.conf import settings
from django.core.management.base import BaseCommand

from arcms.audit.services import acting_as
from arcms.core.jobs import worker_loop


class Command(BaseCommand):
    help = "العامل الخلفي: النشر المجدول، التوزيع على القنوات، النشرة اليومية، تجميع الأرقام، النسخ الاحتياطي."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="دورة واحدة ثم الخروج (للاختبار أو cron)")
        parser.add_argument("--interval", type=int, default=settings.ARCMS_WORKER_INTERVAL)

    def handle(self, *args, **opts):
        self.stdout.write(self.style.SUCCESS("بدأ العامل الخلفي."))
        with acting_as(label="العامل الخلفي"):
            worker_loop(interval=opts["interval"], once=opts["once"], stdout=self.stdout)
