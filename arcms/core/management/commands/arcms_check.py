from django.core.management.base import BaseCommand

from arcms.core.health import run_checks

ICONS = {"ok": "✓", "warn": "!", "fail": "✗"}


class Command(BaseCommand):
    help = "فحص جاهزية الخادم: الإعدادات، التشفير، القاعدة، البحث، العامل، النسخ، القنوات."

    def add_arguments(self, parser):
        parser.add_argument("--live", action="store_true", help="اختبر الاتصال الفعلي بتيليجرام والبريد")

    def handle(self, *args, **opts):
        checks = run_checks(live=opts["live"])
        for c in checks:
            style = {"ok": self.style.SUCCESS, "warn": self.style.WARNING, "fail": self.style.ERROR}[c.level]
            self.stdout.write(style(f"[{ICONS[c.level]}] {c.area}: {c.message}"))
            if c.hint:
                self.stdout.write(f"      ← {c.hint}")
        fails = sum(c.level == "fail" for c in checks)
        warns = sum(c.level == "warn" for c in checks)
        self.stdout.write(f"\nالنتيجة: {len(checks) - fails - warns} سليم، {warns} تنبيه، {fails} خلل.")
        if fails:
            raise SystemExit(1)
