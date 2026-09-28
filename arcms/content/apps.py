from django.apps import AppConfig


class ContentConfig(AppConfig):
    name = "arcms.content"
    label = "content"
    verbose_name = "المحتوى"

    def ready(self):
        from arcms.audit.services import register

        from . import models, signals

        signals.connect()
        register(models.Article, exclude=("word_count", "distributed_at", "content_updated_at"))
        for m in (
            models.Category,
            models.Tag,
            models.Author,
            models.Dossier,
            models.BreakingNews,
            models.LiveCoverage,
            models.LiveEntry,
            models.Page,
            models.MediaAsset,
        ):
            register(m)
