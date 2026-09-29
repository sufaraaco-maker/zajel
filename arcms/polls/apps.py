from django.apps import AppConfig


class PollsConfig(AppConfig):
    name = "arcms.polls"
    label = "polls"
    verbose_name = "استطلاعات القرّاء"

    def ready(self):
        from arcms.audit.services import register

        from . import models

        register(models.Poll, exclude=("total_votes",))
