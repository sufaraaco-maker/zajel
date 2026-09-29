from django.apps import AppConfig


class WiresConfig(AppConfig):
    name = "arcms.wires"
    label = "wires"
    verbose_name = "مكتب الوكالات"

    def ready(self):
        from arcms.audit.services import register

        from . import models

        # تغييرات المصادر تُسجَّل؛ حقول الجلب الدوري لا (تتغير كل بضع دقائق)
        register(models.WireSource, exclude=("last_polled_at", "last_ok_at", "last_error", "etag", "modified"))
