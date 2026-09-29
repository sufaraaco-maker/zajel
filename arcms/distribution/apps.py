from django.apps import AppConfig


class DistributionConfig(AppConfig):
    name = "arcms.distribution"
    label = "distribution"
    verbose_name = "التوزيع"

    def ready(self):
        from django.db.models.signals import post_save

        from arcms.content.signals import invalidate_public_cache

        from .models import ChannelConfig

        # تفعيل الإشعارات أو إيقافها يغيّر زر الجرس في الصفحات المخزنة للقرّاء
        post_save.connect(invalidate_public_cache, sender=ChannelConfig, dispatch_uid="arcms-inv-channelconfig")
