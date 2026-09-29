from django.apps import AppConfig


class PollsConfig(AppConfig):
    name = "arcms.polls"
    label = "polls"
    verbose_name = "استطلاعات القرّاء"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from arcms.audit.services import register
        from arcms.content.signals import invalidate_public_cache

        from . import models

        register(models.Poll, exclude=("total_votes",))
        # إنشاء استطلاع أو إغلاقه يغيّر الصفحات المخزنة (الأصوات نفسها عدّادات تُحدَّث دون حفظ)
        for m in (models.Poll, models.PollOption):
            post_save.connect(invalidate_public_cache, sender=m, dispatch_uid=f"arcms-inv-{m.__name__}")
            post_delete.connect(invalidate_public_cache, sender=m, dispatch_uid=f"arcms-invd-{m.__name__}")
