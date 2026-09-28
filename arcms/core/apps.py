from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "arcms.core"
    label = "core"
    verbose_name = "الموقع"

    def ready(self):
        from django.db.models.signals import post_delete, post_save

        from arcms.audit.services import register
        from arcms.content.signals import invalidate_public_cache

        from . import models

        register(models.SiteSettings)
        for m in (models.MenuItem, models.HomeBlock, models.AdSlot):
            register(m)
        for m in (models.SiteSettings, models.MenuItem, models.HomeBlock, models.AdSlot):
            post_save.connect(invalidate_public_cache, sender=m, dispatch_uid=f"arcms-inv-{m.__name__}")
            post_delete.connect(invalidate_public_cache, sender=m, dispatch_uid=f"arcms-invd-{m.__name__}")
