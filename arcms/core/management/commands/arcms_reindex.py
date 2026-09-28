from django.core.management.base import BaseCommand

from arcms.content.search import rebuild_index


class Command(BaseCommand):
    help = "يعيد بناء فهرس البحث العربي لكل المواد (بعد الاستيراد أو تحديث المحلّل اللغوي)."

    def handle(self, *args, **opts):
        n = rebuild_index(stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f"فُهرست {n} مادة."))
