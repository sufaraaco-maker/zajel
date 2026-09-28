import os
from pathlib import Path

from django.core.management.base import BaseCommand

from arcms.backups import crypto


class Command(BaseCommand):
    help = "يولّد زوج مفاتيح النسخ الاحتياطي. العام للخادم، والخاص يُحفظ خارجه."

    def add_arguments(self, parser):
        parser.add_argument("--private-out", default="arcms-backup-private.key", help="أين يُكتب المفتاح الخاص")

    def handle(self, *args, **opts):
        pub, priv = crypto.generate_keypair()
        out = Path(opts["private_out"])
        if out.exists():
            self.stderr.write(self.style.ERROR(f"{out} موجود مسبقاً؛ لن نكتب فوقه."))
            raise SystemExit(1)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(priv + "\n")
        self.stdout.write(self.style.SUCCESS(f"كُتب المفتاح الخاص في: {out.resolve()}"))
        self.stdout.write(self.style.WARNING(
            "انقله فوراً إلى مكان آمن خارج الخادم (حاسوب غير متصل، خزنة، مدير كلمات مرور) واحذفه من هنا.\n"
            "من دونه لا يمكن استعادة أي نسخة. من يملكه يستطيع قراءة كل النسخ."
        ))
        self.stdout.write("أضف هذا السطر إلى ملف .env:")
        self.stdout.write(f"ARCMS_BACKUP_PUBLIC_KEY={pub}")
        self.stdout.write(f"بصمة المفتاح: {crypto.fingerprint(pub)}")
