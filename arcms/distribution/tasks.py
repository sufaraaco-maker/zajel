"""معالجات مهام التوزيع والنشرة اليومية."""

from __future__ import annotations

from django.utils import timezone

from arcms.core.jobs import PermanentError, enqueue, handler, periodic
from arcms.core.utils import absolute_url

from . import channels
from .models import Channel, ChannelConfig, Delivery, PushSubscription, WhatsAppSubscriber


def _content(delivery: Delivery) -> dict:
    """نص موحّد للمادة أو العاجل. الصورة بطاقة المشاركة بهوية الموقع إن فُعّلت، وإلا صورة المادة."""
    from arcms.content.cards import article_card_url, breaking_card_url
    from arcms.core.models import SiteSettings

    site = SiteSettings.load()
    if delivery.article_id:
        a = delivery.article
        image = article_card_url(a, site) or (a.featured_image.social if a.featured_image_id else "")
        return {
            "title": a.title,
            "summary": a.subtitle or a.summary,
            "url": absolute_url(a.get_short_url()),
            "kicker": "عاجل" if a.is_breaking else a.display_kicker,
            "category": a.category.name if a.category_id else "",
            "image": absolute_url(image) if image else "",
            "tag": f"article-{a.pk}",
        }
    b = delivery.breaking
    url = b.get_url()
    card = breaking_card_url(b, site)
    return {
        "title": b.text,
        "summary": "",
        "url": absolute_url(url) if url else absolute_url("/"),
        "kicker": "عاجل",
        "category": "",
        "image": absolute_url(card) if card else "",
        "tag": f"breaking-{b.pk}",
    }


def _finish(delivery: Delivery, status: str, **fields) -> None:
    delivery.status = status
    delivery.sent_at = timezone.now() if status == Delivery.Status.SENT else delivery.sent_at
    for k, v in fields.items():
        setattr(delivery, k, v)
    delivery.save()


def _run(delivery_id: int, fn):
    delivery = Delivery.objects.select_related("article__featured_image", "article__category", "breaking").get(
        pk=delivery_id
    )
    if delivery.status == Delivery.Status.SENT:
        return {"skipped": "already sent"}
    try:
        return fn(delivery)
    except PermanentError as exc:
        _finish(delivery, Delivery.Status.FAILED, error=str(exc)[:1000])
        raise
    except channels.RetryableError as exc:
        delivery.error = str(exc)[:1000]
        delivery.save(update_fields=["error"])
        raise


@handler("dist.telegram")
def send_telegram(payload):
    def go(delivery: Delivery):
        config = ChannelConfig.get(Channel.TELEGRAM)
        c = _content(delivery)
        text = channels.render_message(
            config.message_template, title=c["title"], summary=c["summary"], url=c["url"],
            kicker=c["kicker"], category=c["category"],
        )
        photo = c["image"] if config.with_image and c["image"].startswith("https://") else None
        message_id = channels.telegram_send(delivery.target, text, photo, silent=config.is_silent_now())
        _finish(delivery, Delivery.Status.SENT, external_id=message_id, recipients=1, error="")
        return {"message_id": message_id}

    return _run(payload["delivery"], go)


@handler("dist.facebook")
def send_facebook(payload):
    def go(delivery: Delivery):
        c = _content(delivery)
        post_id = channels.facebook_post(
            channels.social_text(title=c["title"], summary=c["summary"], url=c["url"], kicker=c["kicker"]), c["url"]
        )
        _finish(delivery, Delivery.Status.SENT, external_id=post_id, recipients=1, error="")
        return {"post_id": post_id}

    return _run(payload["delivery"], go)


@handler("dist.x")
def send_x(payload):
    def go(delivery: Delivery):
        c = _content(delivery)
        text = channels.social_text(title=c["title"], summary="", url=c["url"], kicker=c["kicker"], limit=channels.X_LIMIT)
        post_id = channels.x_post(text)
        _finish(delivery, Delivery.Status.SENT, external_id=post_id, recipients=1, error="")
        return {"post_id": post_id}

    return _run(payload["delivery"], go)


@handler("dist.whatsapp")
def send_whatsapp(payload):
    def go(delivery: Delivery):
        config = ChannelConfig.get(Channel.WHATSAPP)
        c = _content(delivery)
        sent = failed = 0
        last_error = ""
        for sub in WhatsAppSubscriber.objects.filter(is_active=True).iterator():
            try:
                channels.whatsapp_send_template(
                    sub.phone, config.whatsapp_template, config.whatsapp_language, [c["title"], c["url"]]
                )
                sent += 1
            except PermanentError as exc:
                if not sent and not failed:
                    raise  # خطأ في الإعداد نفسه: لا داعي لتجربة بقية الأرقام
                failed += 1
                last_error = str(exc)
            except channels.RetryableError as exc:
                failed += 1
                last_error = str(exc)
        status = Delivery.Status.SENT if sent or not failed else Delivery.Status.FAILED
        _finish(delivery, status, recipients=sent, failures=failed, error=last_error[:1000])
        return {"sent": sent, "failed": failed}

    return _run(payload["delivery"], go)


@handler("dist.push")
def send_push(payload):
    def go(delivery: Delivery):
        from arcms.core.models import SiteSettings

        site = SiteSettings.load()
        c = _content(delivery)
        icon = site.logo.thumb if site.logo_id else "/static/img/icon-192.png"
        data = {
            "title": (f"{c['kicker']} | " if c["kicker"] else "") + site.short_name,
            "body": c["title"],
            "url": c["url"],
            "icon": absolute_url(icon),
            "image": c["image"],
            "tag": c["tag"],
        }
        sent = failed = 0
        for sub in PushSubscription.objects.filter(is_active=True).iterator():
            try:
                channels.push_send(sub, data)
                sent += 1
                PushSubscription.objects.filter(pk=sub.pk).update(last_success_at=timezone.now(), failures=0)
            except channels.SubscriptionGone:
                PushSubscription.objects.filter(pk=sub.pk).update(is_active=False)
            except channels.RetryableError:
                failed += 1
                PushSubscription.objects.filter(pk=sub.pk).update(failures=sub.failures + 1)
        _finish(delivery, Delivery.Status.SENT, recipients=sent, failures=failed, error="")
        return {"sent": sent, "failed": failed}

    return _run(payload["delivery"], go)


# --- النشرة اليومية ---


@periodic("newsletter", 300)
def schedule_newsletter():
    from arcms.core.models import SiteSettings

    site = SiteSettings.load()
    config = ChannelConfig.get(Channel.NEWSLETTER)
    if not (site.newsletter_enabled and config.enabled):
        return
    now = timezone.localtime()
    if now.hour < site.newsletter_hour:
        return
    enqueue("newsletter.send", {"date": now.date().isoformat()}, dedupe_key=f"newsletter:{now.date().isoformat()}")


@handler("newsletter.send")
def send_newsletter(payload):
    from datetime import date

    from .newsletter import build_issue, send_issue

    issue = build_issue(date.fromisoformat(payload["date"]))
    if issue is None:
        return {"skipped": "لا مواد جديدة"}
    return send_issue(issue)
