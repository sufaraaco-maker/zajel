"""قرار ما يُرسل وأين عند النشر أو عند إطلاق عاجل."""

from __future__ import annotations

from django.utils import timezone

from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.core.jobs import enqueue

from .models import Channel, ChannelConfig, Delivery


def _queue(channel: str, *, article=None, breaking=None, target: str = "") -> Delivery:
    delivery = Delivery.objects.create(channel=channel, article=article, breaking=breaking, target=target)
    enqueue(f"dist.{channel}", {"delivery": delivery.pk})
    return delivery


def on_article_published(article_id: int, force: bool = False, only: set[str] | None = None) -> list[Delivery]:
    from arcms.content.models import Article, Status

    article = Article.objects.filter(pk=article_id).first()
    if not article or article.status != Status.PUBLISHED:
        return []
    if article.distributed_at and not force:
        return []  # إعادة النشر بعد سحب أو تعديل لا تُغرق القنوات مرة أخرى
    created: list[Delivery] = []
    flags = {
        Channel.TELEGRAM: article.send_telegram,
        Channel.WHATSAPP: article.send_whatsapp,
        Channel.PUSH: article.send_push or article.is_breaking,
    }
    for channel, wanted in flags.items():
        if only is not None and channel not in only:
            continue
        if not wanted and not force:
            continue
        config = ChannelConfig.get(channel)
        if not config.accepts(article.kind, article.is_breaking) and not (force and config.enabled):
            continue
        if channel == Channel.TELEGRAM:
            for chat in config.chat_ids():
                created.append(_queue(channel, article=article, target=chat))
        elif channel == Channel.PUSH:
            from arcms.core.models import SiteSettings

            if SiteSettings.load().push_enabled:
                created.append(_queue(channel, article=article, target="كل المشتركين"))
        else:
            created.append(_queue(channel, article=article, target="كل المشتركين"))
    if created or not article.distributed_at:
        Article.objects.filter(pk=article.pk).update(distributed_at=timezone.now())
    if created:
        record(
            Action.DISTRIBUTE,
            article,
            message="أُرسلت إلى: " + "، ".join(sorted({d.get_channel_display() for d in created})),
        )
    return created


def on_breaking_created(breaking_id: int) -> list[Delivery]:
    from arcms.content.models import BreakingNews

    item = BreakingNews.objects.filter(pk=breaking_id).first()
    if not item:
        return []
    created = []
    if item.send_telegram:
        config = ChannelConfig.get(Channel.TELEGRAM)
        if config.enabled:
            for chat in config.chat_ids():
                created.append(_queue(Channel.TELEGRAM, breaking=item, target=chat))
    if item.send_push and ChannelConfig.get(Channel.PUSH).enabled:
        created.append(_queue(Channel.PUSH, breaking=item, target="كل المشتركين"))
    if item.send_whatsapp and ChannelConfig.get(Channel.WHATSAPP).enabled:
        created.append(_queue(Channel.WHATSAPP, breaking=item, target="كل المشتركين"))
    if created:
        record(Action.DISTRIBUTE, item, message="عاجل أُرسل إلى القنوات")
    return created
