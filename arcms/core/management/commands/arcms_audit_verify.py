from django.core.management.base import BaseCommand

from arcms.audit.services import check_anchor, current_anchor, verify_chain


class Command(BaseCommand):
    help = "يتحقق من سلامة سلسلة سجل التدقيق، ومن مراسٍ محفوظة خارج الخادم."

    def add_arguments(self, parser):
        parser.add_argument("--anchor", action="store_true", help="اطبع مرساة السجل الحالية (الرقم:التجزئة) لحفظها خارج الخادم")
        parser.add_argument("--expect", action="append", default=[], metavar="ID:HASH",
                            help="مرساة محفوظة سابقاً يجب أن تبقى مطابقة (يمكن تكرارها)")

    def handle(self, *args, **opts):
        if opts["anchor"]:
            self.stdout.write(current_anchor())
            return
        failed = False
        for anchor in opts["expect"]:
            ok, message = check_anchor(anchor)
            self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(message))
            failed |= not ok
        ok, count, broken = verify_chain()
        if ok:
            self.stdout.write(self.style.SUCCESS(f"السلسلة سليمة: {count} قيد."))
        else:
            self.stdout.write(self.style.ERROR(f"السلسلة مكسورة عند القيد #{broken.pk} ({broken.created_at})."))
            failed = True
        if failed:
            raise SystemExit(1)
