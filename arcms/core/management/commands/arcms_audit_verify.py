from django.core.management.base import BaseCommand

from arcms.audit.services import verify_chain


class Command(BaseCommand):
    help = "يتحقق من سلامة سلسلة سجل التدقيق (لم يُعدَّل أو يُحذف أي قيد)."

    def handle(self, *args, **opts):
        ok, count, broken = verify_chain()
        if ok:
            self.stdout.write(self.style.SUCCESS(f"السلسلة سليمة: {count} قيد."))
        else:
            self.stdout.write(self.style.ERROR(f"السلسلة مكسورة عند القيد #{broken.pk} ({broken.created_at})."))
            raise SystemExit(1)
