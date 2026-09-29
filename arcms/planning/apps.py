from django.apps import AppConfig


class PlanningConfig(AppConfig):
    name = "arcms.planning"
    label = "planning"
    verbose_name = "خطة التغطية"

    def ready(self):
        from arcms.audit.services import register

        from . import models, signals  # noqa: F401 - ربط المادة بمهمتها

        register(models.Assignment, exclude=("updated_at",))
