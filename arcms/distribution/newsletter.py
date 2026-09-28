"""النشرة البريدية اليومية: تُبنى من أبرز مواد آخر 24 ساعة وتُرسل لكل مشترك مؤكَّد."""

from __future__ import annotations

from datetime import date, timedelta

from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import F
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from arcms.arabic.dates import format_date
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.core.utils import absolute_url

from .models import NewsletterIssue, NewsletterSubscriber


def pick_articles(day: date, count: int):
    from datetime import datetime, time

    from arcms.content.models import Article

    if day >= timezone.localdate():
        end = timezone.now()  # نشرة اليوم: آخر 24 ساعة حتى لحظة الإرسال
    else:
        end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
    start = end - timedelta(hours=24)
    return list(
        Article.objects.published()
        .filter(in_newsletter=True, published_at__gte=start, published_at__lt=end)
        .select_related("category", "featured_image")
        .order_by(F("priority").desc(), F("view_count").desc(), "-published_at")[:count]
    )


def build_issue(day: date, force: bool = False) -> NewsletterIssue | None:
    from arcms.core.models import SiteSettings

    existing = NewsletterIssue.objects.filter(date=day).first()
    if existing and not force:
        return existing
    site = SiteSettings.load()
    articles = pick_articles(day, site.newsletter_count)
    if not articles:
        return None
    subject = f"{site.short_name}: أبرز أخبار {format_date(day, site.date_style)}"
    ctx = {
        "site": site,
        "articles": articles,
        "day_label": format_date(day, site.date_style),
        "home_url": absolute_url("/"),
        "unsubscribe_url": "{{unsubscribe_url}}",
    }
    html = render_to_string("distribution/newsletter_email.html", ctx)
    text = render_to_string("distribution/newsletter_email.txt", ctx)
    issue, _ = NewsletterIssue.objects.update_or_create(
        date=day,
        defaults={"subject": subject, "html": html, "text": text, "article_ids": [a.pk for a in articles]},
    )
    return issue


def send_issue(issue: NewsletterIssue, batch: int = 50) -> dict:
    subscribers = NewsletterSubscriber.objects.filter(confirmed_at__isnull=False, unsubscribed_at__isnull=True)
    sent = failed = 0
    connection = get_connection()
    messages = []

    def flush():
        nonlocal sent, failed, messages
        if not messages:
            return
        try:
            sent += connection.send_messages(messages) or 0
        except Exception:  # noqa: BLE001
            failed += len(messages)
        messages = []

    for sub in subscribers.iterator():
        unsub = absolute_url(reverse("public:newsletter_unsubscribe", args=[sub.token]))
        msg = EmailMultiAlternatives(
            subject=issue.subject,
            body=issue.text.replace("{{unsubscribe_url}}", unsub),
            to=[sub.email],
            headers={"List-Unsubscribe": f"<{unsub}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"},
        )
        msg.attach_alternative(issue.html.replace("{{unsubscribe_url}}", unsub), "text/html")
        messages.append(msg)
        if len(messages) >= batch:
            flush()
    flush()
    issue.sent_count = sent
    issue.failed_count = failed
    issue.sent_at = timezone.now()
    issue.save()
    record(Action.DISTRIBUTE, issue, message=f"النشرة اليومية: {sent} مرسلة، {failed} فاشلة", object_repr=issue.subject)
    return {"sent": sent, "failed": failed}


def send_confirmation(sub: NewsletterSubscriber) -> None:
    from django.core.mail import send_mail

    from arcms.core.models import SiteSettings

    site = SiteSettings.load()
    link = absolute_url(reverse("public:newsletter_confirm", args=[sub.token]))
    send_mail(
        f"أكّد اشتراكك في نشرة {site.short_name}",
        f"مرحباً،\n\nلتأكيد اشتراكك في النشرة اليومية افتح الرابط:\n{link}\n\nإن لم تطلب الاشتراك فتجاهل هذه الرسالة.",
        None,
        [sub.email],
    )
