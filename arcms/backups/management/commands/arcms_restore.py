import getpass
import json
from pathlib import Path

from django.core.management.base import BaseCommand

from arcms.audit.models import Action
from arcms.audit.services import acting_as, record
from arcms.backups.service import inspect_backup, restore_backup


class Command(BaseCommand):
    help = "يتحقق من نسخة مشفّرة أو يستعيدها. الاستعادة تستبدل قاعدة البيانات والوسائط الحالية."

    def add_arguments(self, parser):
        parser.add_argument("file")
        parser.add_argument("--key", help="ملف المفتاح الخاص")
        parser.add_argument("--passphrase", action="store_true", help="النسخة مشفّرة بعبارة مرور")
        parser.add_argument("--verify-only", action="store_true", help="فك وتحقق كامل دون استعادة")
        parser.add_argument("--no-media", action="store_true")
        parser.add_argument("--yes", action="store_true", help="تأكيد الاستعادة دون سؤال")

    def handle(self, *args, **opts):
        path = Path(opts["file"])
        private = Path(opts["key"]).read_text().strip() if opts.get("key") else None
        passphrase = getpass.getpass("عبارة المرور: ") if opts["passphrase"] else None
        manifest = inspect_backup(path, private_key=private, passphrase=passphrase)
        self.stdout.write(self.style.SUCCESS("النسخة سليمة وفُكّ تشفيرها بنجاح:"))
        self.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2))
        if opts["verify_only"]:
            return
        if not opts["yes"]:
            answer = input("ستُستبدل قاعدة البيانات والوسائط الحالية بالكامل. اكتب «استعادة» للمتابعة: ")
            if answer.strip() != "استعادة":
                self.stdout.write("أُلغيت.")
                return
        restore_backup(path, private_key=private, passphrase=passphrase, restore_media=not opts["no_media"])
        with acting_as(label="سطر الأوامر"):
            record(Action.BACKUP, message=f"استعادة من {path.name}", object_repr=path.name)
        self.stdout.write(self.style.SUCCESS("اكتملت الاستعادة. شغّل manage.py migrate ثم arcms_reindex ثم arcms_check."))
