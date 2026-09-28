from django.core.management.base import BaseCommand

from arcms.audit.services import acting_as
from arcms.backups.service import BackupError, create_backup


class Command(BaseCommand):
    help = "ينشئ نسخة احتياطية مشفّرة (قاعدة البيانات + الوسائط)."

    def add_arguments(self, parser):
        parser.add_argument("--no-media", action="store_true")

    def handle(self, *args, **opts):
        try:
            with acting_as(label="سطر الأوامر"):
                rec = create_backup(include_media=not opts["no_media"], trigger="سطر الأوامر")
        except BackupError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            raise SystemExit(1)
        self.stdout.write(self.style.SUCCESS(f"{rec.filename} ({rec.size} بايت) SHA-256: {rec.sha256}"))
