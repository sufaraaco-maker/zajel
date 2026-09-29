"""تجهيز بيانات كتل الصفحة الرئيسية."""

from __future__ import annotations

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from arcms.content.models import Article, ArticleKind, BreakingNews, LiveCoverage
from arcms.core.models import HomeBlock, HomeBlockArticle, SiteSettings


def published():
    return Article.objects.public_list()


def most_read(limit: int = 6, days: int = 2) -> list[Article]:
    from arcms.analytics.queries import most_read_ids

    ids = most_read_ids(days=days, limit=limit)
    by_id = {a.pk: a for a in published().filter(pk__in=ids)}
    items = [by_id[i] for i in ids if i in by_id][:limit]
    if len(items) < limit:  # موقع جديد بلا أرقام بعد: نكمل بالأحدث
        extra = published().exclude(pk__in=[a.pk for a in items]).order_by("-view_count", "-published_at")
        items += list(extra[: limit - len(items)])
    return items


def hero_articles(count: int) -> list[Article]:
    now = timezone.now()
    featured = list(
        published()
        .filter(is_featured=True)
        .filter(Q(featured_until__isnull=True) | Q(featured_until__gt=now))
        .order_by("-priority", "-published_at")[:count]
    )
    if len(featured) < count:
        featured += list(published().exclude(pk__in=[a.pk for a in featured])[: count - len(featured)])
    return featured


def build_blocks() -> list[dict]:
    seen: set[int] = set()
    hero_ids: set[int] = set()
    out = []
    blocks = HomeBlock.objects.filter(is_active=True).select_related("category", "dossier", "ad_slot__image").prefetch_related("categories")
    for block in blocks:
        ctx = {"block": block, "template": f"public/blocks/{block.kind}.html", "title": block.title or block.get_kind_display()}
        n = block.count or 6
        if block.kind == HomeBlock.Kind.HERO:
            items = hero_articles(n)
            seen.update(a.pk for a in items)
            hero_ids = {a.pk for a in items}
            ctx["lead"], ctx["items"] = (items[0] if items else None), items[1:]
            if block.layout not in (HomeBlock.Layout.GRID, HomeBlock.Layout.LIST):
                ctx["latest_timeline"] = list(published()[:7])
        elif block.kind == HomeBlock.Kind.LATEST:
            ctx["items"] = list(published()[:n])
        elif block.kind == HomeBlock.Kind.CATEGORY and block.category_id:
            qs = published().filter(Q(category_id__in=block.category.family_ids()) | Q(extra_categories=block.category)).distinct()
            items = list(qs.exclude(pk__in=seen)[:n])
            seen.update(a.pk for a in items)
            ctx["items"] = items
            ctx["more_url"] = block.category.get_absolute_url()
            ctx["title"] = block.title or block.category.name
        elif block.kind == HomeBlock.Kind.COLUMNS:
            columns = []
            for cat in block.categories.all():
                items = list(published().filter(category_id__in=cat.family_ids()).exclude(pk__in=seen)[:n])
                seen.update(a.pk for a in items)
                columns.append({"category": cat, "items": items})
            ctx["columns"] = columns
        elif block.kind == HomeBlock.Kind.MOST_READ:
            ctx["items"] = most_read(n)
            ctx["tabs"] = [("اليوم", ctx["items"] if n else []), ("هذا الأسبوع", most_read(n, days=7))]
        elif block.kind == HomeBlock.Kind.OPINION:
            kind = block.article_kind or ArticleKind.OPINION
            ctx["items"] = list(published().filter(kind=kind).prefetch_related("authors__photo")[:n])
            ctx["more_url"] = reverse("public:kind", args=[kind])
        elif block.kind in (HomeBlock.Kind.VIDEO, HomeBlock.Kind.KIND):
            default = ArticleKind.VIDEO if block.kind == HomeBlock.Kind.VIDEO else ArticleKind.REPORT
            kind = block.article_kind or default
            # كتل الأنواع واجهات عرض لشكل المادة: لا تستبعد ما ظهر في كتل الأقسام.
            items = list(published().filter(kind=kind).exclude(pk__in=hero_ids)[:n])
            ctx["items"] = items
            ctx["more_url"] = reverse("public:kind", args=[kind])
            ctx["title"] = block.title or dict(ArticleKind.choices).get(kind, "")
        elif block.kind == HomeBlock.Kind.DOSSIER and block.dossier_id:
            ctx["dossier"] = block.dossier
            ctx["items"] = list(published().filter(dossiers=block.dossier)[:n])
            ctx["title"] = block.title or block.dossier.title
        elif block.kind == HomeBlock.Kind.LIVE:
            live = LiveCoverage.objects.filter(is_live=True).first()
            if not live:
                continue
            ctx["live"] = live
            ctx["entries"] = list(live.entries.select_related("image")[:4])
        elif block.kind == HomeBlock.Kind.AD:
            if not block.ad_slot or not block.ad_slot.is_active:
                continue
            ctx["ad"] = block.ad_slot
        elif block.kind == HomeBlock.Kind.BRIEF:
            ctx.update(_brief(block, n))
            if not ctx["briefs"] and not ctx["audio"]:
                continue
        elif block.kind == HomeBlock.Kind.PICKS:
            picks = HomeBlockArticle.objects.filter(block=block).select_related("article__featured_image", "article__category")
            items = [p.article for p in picks if p.article.is_live][:n]
            if not items:  # لم يختر المحرر شيئاً بعد: المواد المميزة بعد الواجهة
                items = list(published().filter(is_featured=True).exclude(pk__in=hero_ids)[:n])
            ctx["items"] = items
        elif block.kind == HomeBlock.Kind.PROMO:
            ctx["link"] = block.safe_link()
            ctx["qr_svg"] = _qr(ctx["link"]) if ctx["link"] else ""
            ctx["title"] = block.title
        elif block.kind == HomeBlock.Kind.STATS:
            ctx["stats"] = block.item_pairs()
            if not ctx["stats"]:
                continue
        elif block.kind == HomeBlock.Kind.PLATFORMS:
            ctx["platforms"] = _platforms(block)
            if not ctx["platforms"]:
                continue
        elif block.kind == HomeBlock.Kind.POLL:
            from arcms.polls.models import Poll
            from arcms.polls.services import render_poll

            poll = (Poll.objects.filter(is_open=True).filter(Q(closes_at__isnull=True) | Q(closes_at__gt=timezone.now()))
                    .prefetch_related("options").first())
            if poll is None:
                continue
            ctx["poll_html"] = render_poll(poll)
        elif block.kind in (HomeBlock.Kind.NEWSLETTER, HomeBlock.Kind.HTML):
            pass
        else:
            continue
        if "items" in ctx and not ctx["items"] and block.kind != HomeBlock.Kind.HERO:
            continue
        out.append(ctx)
    return out


def _brief(block: HomeBlock, n: int) -> dict:
    """موجز: آخر العاجل خلال يوم (أو آخر العناوين)، وآخر حلقة صوتية إن وُجدت."""
    briefs = [
        {"time": b.created_at, "text": b.text, "url": b.get_url()} for b in BreakingNews.current(hours=24)[:n]
    ]
    if len(briefs) < n:
        briefs += [
            {"time": a.published_at, "text": a.title, "url": a.get_absolute_url()}
            for a in published()[: n - len(briefs)]
        ]
    podcasts = published().filter(kind=ArticleKind.PODCAST).exclude(audio="")
    if block.category_id:
        podcasts = podcasts.filter(category_id__in=block.category.family_ids())
    return {"briefs": briefs, "audio": podcasts.first(), "title": block.title or "موجز الأخبار"}


def _qr(link: str) -> str:
    import segno

    from arcms.core.utils import absolute_url

    target = absolute_url(link) if link.startswith("/") else link
    return segno.make(target, error="m").svg_inline(scale=4, border=1, dark="#111418", light="#ffffff")


def _platforms(block: HomeBlock) -> list[dict]:
    followers = {k.strip().lower(): v for k, v in block.item_pairs()}
    aliases = {"twitter": "x", "تيليجرام": "telegram", "واتساب": "whatsapp", "إكس": "x", "فيسبوك": "facebook",
               "إنستغرام": "instagram", "يوتيوب": "youtube", "تيك توك": "tiktok"}
    followers = {aliases.get(k, k): v for k, v in followers.items()}
    return [
        {"key": key, "label": label, "url": url, "followers": followers.get(key, "")}
        for key, label, url in SiteSettings.load().social_links
    ]
