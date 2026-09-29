"""جلب المصادر المستحقة، وتعليم التنبيهات، واعتماد مادة الوكالة مسودةً، وتنظيف القديم."""

from __future__ import annotations

import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.html import escape

from arcms.arabic.normalize import normalize
from arcms.audit.models import Action
from arcms.audit.services import record

from . import feeds
from .models import WireItem, WireKeyword, WireSource

POLL_BUDGET_SECONDS = 45


def watchwords() -> list[str]:
    return [w for w in (normalize(k).strip() for k in WireKeyword.objects.values_list("word", flat=True)) if w]


def is_alert(text: str, words: list[str]) -> bool:
    norm = normalize(text)
    return any(w in norm for w in words)


def set_watchwords(text: str) -> list[str]:
    """يستبدل كلمات التنبيه (سطر لكل كلمة) ويعيد تقييم المواد الجديدة في آخر يوم."""
    words = []
    for line in text.replace("،", "\n").replace(",", "\n").splitlines():
        word = " ".join(line.split())[:80]
        if word and word not in words:
            words.append(word)
    with transaction.atomic():
        WireKeyword.objects.exclude(word__in=words).delete()
        for word in words:
            WireKeyword.objects.get_or_create(word=word)
    norm = watchwords()
    since = timezone.now() - timedelta(days=1)
    for item in WireItem.objects.filter(status=WireItem.Status.NEW, fetched_at__gte=since).only("pk", "search_text", "is_alert"):
        flag = any(w in item.search_text for w in norm)
        if flag != item.is_alert:
            WireItem.objects.filter(pk=item.pk).update(is_alert=flag)
    return words


def poll(source: WireSource, *, session=None) -> int:
    """يجلب مصدراً واحداً ويضيف الجديد منه. يعيد عدد المواد الجديدة."""
    now = timezone.now()
    source.last_polled_at = now
    try:
        result = feeds.fetch(source.feed_url, etag=source.etag, modified=source.modified, session=session)
        entries = [] if result.not_modified else feeds.parse(result.content)
    except feeds.FeedError as exc:
        source.last_error = str(exc)[:300]
        source.save(update_fields=["last_polled_at", "last_error"])
        return 0
    words = watchwords()
    hashes = [e.guid_hash for e in entries]
    known = set(WireItem.objects.filter(source=source, guid_hash__in=hashes).values_list("guid_hash", flat=True))
    fresh = []
    for e in entries:
        if e.guid_hash in known:
            continue
        known.add(e.guid_hash)
        blob = normalize(f"{e.title} {e.summary}")[:6000]
        fresh.append(WireItem(
            source=source, guid_hash=e.guid_hash, title=e.title, summary=e.summary, body=e.body, link=e.link,
            image_url=e.image_url, published_at=e.published_at or now, fetched_at=now, search_text=blob,
            is_alert=any(w in blob for w in words),
        ))
    WireItem.objects.bulk_create(fresh, ignore_conflicts=True)
    if not result.not_modified:
        source.etag, source.modified = result.etag, result.modified
    source.last_ok_at, source.last_error = now, ""
    source.save(update_fields=["last_polled_at", "last_ok_at", "last_error", "etag", "modified"])
    return len(fresh)


def poll_due(*, session=None, budget: float = POLL_BUDGET_SECONDS) -> int:
    """يجلب المصادر المستحقة، الأقدم جلباً أولاً، في حدود زمنية حتى لا تعطّل بقية مهام العامل."""
    started = time.monotonic()
    total = 0
    now = timezone.now()
    for source in WireSource.objects.filter(is_active=True).order_by("last_polled_at", "id"):
        if time.monotonic() - started > budget:
            break
        if source.is_due(now):
            total += poll(source, session=session)
    return total


def item_body_html(item: WireItem) -> str:
    if item.body:
        return item.body
    paras = [p.strip() for p in item.summary.split("\n") if p.strip()] or [item.summary]
    return "".join(f"<p>{escape(p)}</p>" for p in paras if p)


def adopt(item: WireItem, user):
    """ينشئ مسودة من مادة الوكالة مع نسبتها، أو يعيد المسودة إن سبق اعتمادها."""
    from arcms.content.models import Article, ArticleKind, Status
    from arcms.content.sanitize import sanitize_html

    with transaction.atomic():
        item = WireItem.objects.select_for_update(of=("self",)).select_related("source__category").get(pk=item.pk)
        if item.article_id:
            return item.article
        source = item.source
        article = Article.objects.create(
            kind=ArticleKind.NEWS,
            status=Status.DRAFT,
            title=item.title[:250],
            excerpt=item.summary[:600],
            body=sanitize_html(item_body_html(item)),
            category=source.category,
            source=source.attribution[:150],
            source_url=item.link[:200] if len(item.link) <= 200 else "",
            created_by=user,
            last_edited_by=user,
        )
        item.status, item.article, item.handled_by, item.handled_at = WireItem.Status.ADOPTED, article, user, timezone.now()
        item.save(update_fields=["status", "article", "handled_by", "handled_at"])
    record(Action.CREATE, article, message=f"مسودة من مكتب الوكالات ({source.name})")
    return article


def ignore(ids, user) -> int:
    return WireItem.objects.filter(pk__in=ids, status=WireItem.Status.NEW).update(
        status=WireItem.Status.IGNORED, handled_by=user, handled_at=timezone.now()
    )


def purge_old() -> int:
    """يحذف ما لم يُعتمد بعد مدة الاحتفاظ؛ المعتمد يبقى مرجعاً لمسودته."""
    cutoff = timezone.now() - timedelta(days=max(1, settings.ARCMS_WIRE_RETENTION_DAYS))
    deleted, _ = WireItem.objects.filter(fetched_at__lt=cutoff).exclude(status=WireItem.Status.ADOPTED).delete()
    return deleted
