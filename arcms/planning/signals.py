"""مرحلة المهمة تتبع مادتها: كل حفظ للمادة يحدّث مهامها (إلا الملغاة)."""

from django.db.models.signals import post_save
from django.dispatch import receiver

from arcms.content.models import Article

from .models import Assignment


@receiver(post_save, sender=Article, dispatch_uid="planning-sync-article")
def sync_assignment(sender, instance, raw=False, **kwargs):
    if raw:
        return
    status = Assignment.status_for_article(instance)
    (Assignment.objects.filter(article=instance).exclude(status__in=[status, Assignment.Status.DROPPED])
     .update(status=status))
