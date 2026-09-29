"""صفحات القرّاء."""

from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import cache_control
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from arcms.accounts.middleware import session_fully_verified
from arcms.analytics.collector import record_view
from arcms.arabic.highlight import snippet
from arcms.content import search as search_mod
from arcms.content.models import (
    KIND_PLURALS,
    Article,
    ArticleKind,
    Author,
    BreakingNews,
    Category,
    ContactMessage,
    Dossier,
    LiveCoverage,
    Page,
    Tag,
)
from arcms.content.sanitize import plain_text, render_embeds
from arcms.content.signals import public_cache_version
from arcms.content.workflow import can_view
from arcms.core.models import AdSlot, SiteSettings
from arcms.core.ratelimit import exceeded
from arcms.core.utils import absolute_url, client_ip, json_script_safe
from arcms.distribution.models import NewsletterSubscriber, PushSubscription

from .blocks import build_blocks, most_read, published

PAGE_SIZE = 18

# (عدد، ثوانٍ) لكل مصدر. متساهلة عمداً: قرّاء كثيرون قد يخرجون من عنوان واحد
# خلف شبكات الهاتف المحمول، والغرض صدّ الإغراق الآلي لا القارئ.
RATE_LIMITS = {
    "beacon": (600, 60),
    "push": (60, 3600),
    "newsletter-ip": (30, 3600),
    "newsletter-mail": (3, 86400),
    "contact": (10, 3600),
    # لكل عنوان في كل استطلاع: سخيّ لأن شبكات الجوال تجمع آلاف القرّاء خلف عنوان واحد
    "poll": (40, 3600),
}


def _limited(scope: str, ident: str | None) -> bool:
    limit, window = RATE_LIMITS[scope]
    return exceeded(scope, ident, limit=limit, window=window)


def _paginate(request, qs, size=PAGE_SIZE):
    paginator = Paginator(qs, size)
    try:
        number = int(request.GET.get("page", 1))
    except ValueError:
        number = 1
    return paginator.get_page(number)


def ticker():
    site = SiteSettings.load()
    if not site.ticker_enabled:
        return []
    key = f"arcms:ticker:{public_cache_version()}"
    items = cache.get(key)
    if items is None:
        items = list(BreakingNews.current(site.ticker_hours)[:10])
        cache.set(key, items, 60)
    return items


def _common(extra: dict | None = None) -> dict:
    ctx = {"ticker": ticker(), "ad_header": AdSlot.live_for("header"), "ad_sidebar": AdSlot.live_for("sidebar")}
    if extra:
        ctx.update(extra)
    return ctx


def home(request):
    site = SiteSettings.load()
    key = f"arcms:home:{public_cache_version()}"
    blocks = cache.get(key) if not request.user.is_authenticated else None
    if blocks is None:
        blocks = build_blocks()
        cache.set(key, blocks, site.home_cache_seconds)
    return render(request, "public/home.html", _common({"blocks": blocks, "is_home": True}))


def _related(article: Article, limit: int = 4) -> list[Article]:
    manual = list(published().filter(pk__in=article.related.values("pk"))[:limit])
    if len(manual) >= limit:
        return manual
    tag_ids = list(article.tags.values_list("pk", flat=True)[:5])
    qs = published().exclude(pk=article.pk).exclude(pk__in=[a.pk for a in manual])
    if tag_ids:
        qs = qs.filter(Q(tags__in=tag_ids) | Q(category_id=article.category_id)).distinct()
    else:
        qs = qs.filter(category_id=article.category_id)
    return manual + list(qs.filter(published_at__gte=timezone.now() - timedelta(days=60))[: limit - len(manual)])


def article_detail(request, pk: int, slug: str = ""):
    article = get_object_or_404(
        Article.objects.select_related("category", "featured_image").prefetch_related("tags", "authors__photo"), pk=pk
    )
    preview = False
    if not article.is_live:
        if session_fully_verified(request) and can_view(request.user, article):
            preview = True
        else:
            raise Http404
    if not preview and slug != article.slug:
        return redirect(article.get_absolute_url(), permanent=True)
    gallery = list(article.gallery_items.select_related("media")) if article.kind == ArticleKind.GALLERY else []
    body = render_embeds(article.body)
    if "[poll:" in body:
        from arcms.polls.services import render_shortcodes

        body = render_shortcodes(body, request)
    if article.dateline:
        # مكان الخبر يسبق أول فقرة على السطر نفسه، كما في الصحف: «غزة - خاص: ...»
        from django.utils.html import escape

        tag = f'<span class="dateline">{escape(article.dateline)}:</span> '
        body = body.replace("<p>", "<p>" + tag, 1) if body.startswith("<p>") else tag + body
    ctx = _common(
        {
            "article": article,
            "body_html": body,
            "preview": preview,
            "gallery": gallery,
            "related": _related(article),
            "most_read": most_read(6),
            "latest": list(published().exclude(pk=article.pk)[:6]),
            "share_url": absolute_url(article.get_short_url()),
            "share_card": _share_card(article),
            "canonical": absolute_url(article.get_absolute_url()),
            "ad_inline": AdSlot.live_for("article_inline"),
            "ad_end": AdSlot.live_for("article_end"),
            "json_ld": json_script_safe(_news_article_ld(article)),
            "track_article": article.pk,
            "track_category": article.category_id or "",
        }
    )
    return render(request, "public/article.html", ctx)


def _share_card(article: Article) -> str:
    if not article.is_live:
        return ""
    from arcms.content.cards import article_card_url

    url = article_card_url(article, SiteSettings.load())
    return absolute_url(url) if url else ""


def _news_article_ld(a: Article) -> dict:
    site = SiteSettings.load()
    data = {
        "@context": "https://schema.org",
        "@type": "OpinionNewsArticle" if a.kind == ArticleKind.OPINION else "NewsArticle",
        "headline": a.title[:110],
        "description": a.seo_description or a.summary,
        "datePublished": a.published_at.isoformat() if a.published_at else None,
        "dateModified": (a.content_updated_at or a.published_at or a.updated_at).isoformat(),
        "mainEntityOfPage": absolute_url(a.get_absolute_url()),
        "inLanguage": "ar",
        "publisher": {"@type": "NewsMediaOrganization", "name": site.name, "url": settings.SITE_URL},
        "author": [{"@type": "Person", "name": au.name, "url": absolute_url(au.get_absolute_url())} for au in a.authors.all()]
        or [{"@type": "Organization", "name": site.name}],
    }
    if a.featured_image_id:
        data["image"] = [absolute_url(a.featured_image.social), absolute_url(a.featured_image.large)]
    if site.logo_id:
        data["publisher"]["logo"] = {"@type": "ImageObject", "url": absolute_url(site.logo.url)}
    if a.category_id:
        data["articleSection"] = a.category.name
    return data


def short_link(request, pk: int):
    article = get_object_or_404(Article.objects.only("pk", "slug", "status", "published_at"), pk=pk)
    if not article.is_live:
        raise Http404
    target = article.get_absolute_url()
    query = request.META.get("QUERY_STRING")
    return redirect(f"{target}?{query}" if query else target, permanent=True)


def _card_response(data: bytes) -> HttpResponse:
    resp = HttpResponse(data, content_type="image/jpeg")
    resp["Cache-Control"] = "public, max-age=86400"
    return resp


def article_card(request, pk: int):
    """بطاقة المشاركة للمادة المنشورة فقط؛ المسودات لا تُكشف عناوينها."""
    from arcms.content import cards

    site = SiteSettings.load()
    if not (site.share_cards and cards.available()):
        raise Http404
    article = get_object_or_404(Article.objects.select_related("featured_image", "category"), pk=pk)
    if not article.is_live:
        raise Http404
    return _card_response(cards.article_card(article, site))


def breaking_card(request, pk: int):
    from arcms.content import cards

    site = SiteSettings.load()
    if not (site.share_cards and cards.available()):
        raise Http404
    breaking = get_object_or_404(BreakingNews, pk=pk, is_active=True)
    return _card_response(cards.breaking_card(breaking, site))


def category_detail(request, slug: str):
    category = get_object_or_404(Category.objects.select_related("cover"), slug=slug, is_active=True)
    qs = published().filter(Q(category_id__in=category.family_ids()) | Q(extra_categories=category)).distinct()
    page = _paginate(request, qs)
    return render(
        request,
        "public/listing.html",
        _common(
            {
                "heading": category.name,
                "description": category.description,
                "accent": category.color,
                "cover": category.cover,
                "layout": category.page_layout,
                "page": page,
                "subcategories": category.children.filter(is_active=True),
                "feed_url": reverse("public:feed_category", args=[category.slug]),
                "most_read": most_read(5),
                "track_category": category.pk,
            }
        ),
    )


def tag_detail(request, slug: str):
    tag = get_object_or_404(Tag, slug=slug)
    items = published().filter(tags=tag)
    if not items.exists():
        raise Http404  # وسم على مسودات فقط لا يكشف وجوده لأحد من خارج الغرفة
    page = _paginate(request, items)
    return render(request, "public/listing.html", _common({"heading": f"#{tag.name}", "page": page, "most_read": most_read(5)}))


def author_detail(request, slug: str):
    author = get_object_or_404(Author.objects.select_related("photo"), slug=slug, show_page=True)
    page = _paginate(request, published().filter(authors=author))
    return render(request, "public/author.html", _common({"author": author, "page": page, "most_read": most_read(5)}))


def kind_list(request, kind: str):
    if kind not in ArticleKind.values:
        raise Http404
    page = _paginate(request, published().filter(kind=kind).prefetch_related("authors__photo"))
    template = "public/opinion_list.html" if kind == ArticleKind.OPINION else "public/listing.html"
    return render(
        request,
        template,
        _common(
            {
                "heading": KIND_PLURALS.get(kind, kind),
                "page": page,
                "kind": kind,
                "feed_url": reverse("public:feed_kind", args=[kind]),
                "most_read": most_read(5),
                "dark": kind == ArticleKind.VIDEO,
            }
        ),
    )


def latest_list(request):
    page = _paginate(request, published(), 30)
    return render(request, "public/latest.html", _common({"heading": "آخر الأخبار", "page": page}))


def corrections_list(request):
    """سجل التصحيحات العلني: كل تصحيح منشور بتاريخه ورابط مادته، الأحدث أولاً."""
    qs = published().exclude(correction="").exclude(corrected_at__isnull=True).order_by("-corrected_at", "-pk")
    page = _paginate(request, qs, 30)
    return render(request, "public/corrections.html", _common({"page": page}))


def dossier_detail(request, slug: str):
    dossier = get_object_or_404(Dossier.objects.select_related("cover"), slug=slug, is_active=True)
    page = _paginate(request, published().filter(dossiers=dossier))
    return render(request, "public/dossier.html", _common({"dossier": dossier, "page": page, "most_read": most_read(5)}))


def _record_search(request, raw: str, total: int) -> None:
    """ما يبحث عنه القرّاء، مجمّعاً بلا هوية، ويحترم إعدادات القياس ورغبة «عدم التتبع»."""
    site = SiteSettings.load()
    if not site.analytics_enabled:
        return
    if site.analytics_respect_dnt and (request.META.get("HTTP_DNT") == "1" or request.META.get("HTTP_SEC_GPC") == "1"):
        return
    if request.user.is_authenticated:  # بحث الطاقم ليس بحث القرّاء
        return
    from arcms.analytics.searches import record

    record(raw, total, user_agent=request.META.get("HTTP_USER_AGENT", ""))


def search_view(request):
    raw = request.GET.get("q", "").strip()
    kind = request.GET.get("kind", "")
    category = request.GET.get("category", "")
    period = request.GET.get("period", "")
    try:
        number = max(1, int(request.GET.get("page", 1)))
    except ValueError:
        number = 1
    result = None
    items = []
    if raw:
        qs = Article.objects.all()
        if kind in ArticleKind.values:
            qs = qs.filter(kind=kind)
        if category.isdigit():
            cat = Category.objects.filter(pk=category).first()
            if cat:
                qs = qs.filter(category_id__in=cat.family_ids())
        since = None
        if period in ("1", "7", "30", "365"):
            since = timezone.now() - timedelta(days=int(period))
        result = search_mod.search_articles(raw, queryset=qs, since=since, limit=PAGE_SIZE, offset=(number - 1) * PAGE_SIZE)
        for a in result.articles:
            items.append(
                {
                    "article": a,
                    "title_html": snippet(a.title, result.query, 400),
                    "snippet": snippet(plain_text(a.body) or a.summary, result.query),
                }
            )
    total = result.total if result else 0
    if raw and number == 1 and not (kind or category or period):
        _record_search(request, raw, total)
    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    return render(
        request,
        "public/search.html",
        _common(
            {
                "q": raw,
                "items": items,
                "total": total,
                "page_number": number,
                "has_next": number < pages,
                "has_prev": number > 1,
                "kinds": ArticleKind.choices,
                "categories": Category.objects.filter(is_active=True, parent__isnull=True),
                "sel_kind": kind,
                "sel_category": category,
                "sel_period": period,
            }
        ),
    )


def live_list(request):
    page = _paginate(request, LiveCoverage.objects.select_related("cover"), 12)
    return render(request, "public/live_list.html", _common({"page": page}))


def live_detail(request, slug: str):
    live = get_object_or_404(LiveCoverage.objects.select_related("cover"), slug=slug)
    entries = list(live.entries.select_related("image", "author").order_by("-is_pinned", "-created_at")[:200])
    return render(request, "public/live.html", _common({"live": live, "entries": entries, "most_read": most_read(5)}))


@cache_control(max_age=10)
def live_entries_json(request, slug: str):
    live = get_object_or_404(LiveCoverage, slug=slug)
    try:
        after = int(request.GET.get("after", 0))
    except ValueError:
        after = 0
    entries = live.entries.select_related("image").filter(pk__gt=after).order_by("created_at")[:50]
    html = [
        {"id": e.pk, "html": render_to_string("public/partials/live_entry.html", {"e": e}, request=request)}
        for e in entries
    ]
    return JsonResponse({"is_live": live.is_live, "entries": html})


def page_detail(request, slug: str):
    page = get_object_or_404(Page, slug=slug, is_published=True)
    sent = False
    if request.method == "POST" and page.show_contact_form:
        name = request.POST.get("name", "").strip()[:120]
        body = request.POST.get("body", "").strip()[:5000]
        if request.POST.get("website"):  # فخ للبرامج الآلية
            sent = True
        elif _limited("contact", client_ip(request)):
            messages.error(request, "أرسلت رسائل كثيرة خلال وقت قصير. حاول بعد ساعة.")
        elif name and body:
            ContactMessage.objects.create(
                name=name,
                email=request.POST.get("email", "").strip()[:254],
                subject=request.POST.get("subject", "").strip()[:200],
                body=body,
            )
            sent = True
        else:
            messages.error(request, "الاسم والرسالة مطلوبان.")
    return render(request, "public/page.html", _common({"page_obj": page, "sent": sent}))


# --- النشرة والإشعارات ---


def _back(request) -> str:
    """العودة إلى الصفحة السابقة إن كانت من الموقع نفسه فقط."""
    ref = request.META.get("HTTP_REFERER", "")
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return ref
    return "/"


def _same_origin(request) -> bool:
    """التصويت من صفحات الموقع نفسه فقط (بديل رمز CSRF في الصفحات المخزّنة مؤقتاً)."""
    host = request.get_host()
    origin = request.META.get("HTTP_ORIGIN")
    if origin:
        return url_has_allowed_host_and_scheme(origin + "/", allowed_hosts={host}, require_https=request.is_secure())
    ref = request.META.get("HTTP_REFERER", "")
    return bool(ref) and url_has_allowed_host_and_scheme(ref, allowed_hosts={host}, require_https=request.is_secure())


@csrf_exempt
@require_POST
def poll_vote(request, pk: int):
    from arcms.polls import services
    from arcms.polls.models import Poll

    poll = get_object_or_404(Poll, pk=pk)
    wants_json = "application/json" in request.META.get("HTTP_ACCEPT", "")
    if not _same_origin(request):
        return JsonResponse({"error": "طلب من خارج الموقع."}, status=403)
    ok, error = False, ""
    if services.voted(request, poll):
        error = "سُجّل صوتك في هذا الاستطلاع من قبل."
    elif not poll.is_active:
        error = "أُغلق هذا الاستطلاع."
    elif _limited("poll", f"{client_ip(request)}:{poll.pk}"):
        error = "أصوات كثيرة من شبكتك خلال وقت قصير. حاول لاحقاً."
    else:
        option = request.POST.get("option", "")
        ok = option.isdigit() and services.record_vote(poll, int(option))
        if not ok:
            error = "اختر أحد الخيارات."
    poll.refresh_from_db()
    if wants_json:
        from arcms.arabic.numbers import count_phrase, to_digits

        label = to_digits(count_phrase(poll.total_votes, "صوت واحد", "صوتان", "أصوات", "صوتاً"), SiteSettings.load().digits)
        resp = JsonResponse({"ok": ok, "error": error, "total": poll.total_votes, "total_label": label,
                             "results": poll.results()}, status=200 if ok or not error else 400)
    else:
        (messages.success if ok else messages.error)(request, "شكراً، سُجّل صوتك." if ok else error)
        resp = redirect(_back(request).split("#")[0] + f"#poll-{poll.pk}")
    if ok:
        # علامة في متصفح القارئ فقط (لا معرّف فيها) تمنع التصويت المتكرر وتُظهر النتائج
        resp.set_cookie(services.cookie_name(poll.pk), "1", max_age=90 * 86400, samesite="Lax",
                        secure=request.is_secure(), httponly=False)
    return resp


@require_POST
def newsletter_subscribe(request):
    from django.core.exceptions import ValidationError
    from django.core.validators import validate_email

    from arcms.distribution.newsletter import send_confirmation

    email = request.POST.get("email", "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        messages.error(request, "البريد الإلكتروني غير صالح.")
        return redirect(_back(request))
    if request.POST.get("website"):
        return redirect("/")
    if _limited("newsletter-ip", client_ip(request)):
        messages.error(request, "طلبات كثيرة من جهازك خلال وقت قصير. حاول لاحقاً.")
        return redirect(_back(request))
    sub, created = NewsletterSubscriber.objects.get_or_create(email=email, defaults={"source": "site"})
    if sub.unsubscribed_at:
        sub.unsubscribed_at = None
        sub.confirmed_at = None
        sub.save()
    # لا تتحول صفحة الاشتراك إلى أداة لإغراق بريد أحد برسائل التأكيد.
    if not sub.confirmed_at and not _limited("newsletter-mail", email):
        send_confirmation(sub)
    messages.success(request, "أرسلنا إليك رسالة لتأكيد الاشتراك. افتحها واضغط الرابط.")
    return redirect(_back(request))


def newsletter_confirm(request, token: str):
    sub = get_object_or_404(NewsletterSubscriber, token=token)
    if not sub.confirmed_at:
        sub.confirmed_at = timezone.now()
        sub.unsubscribed_at = None
        sub.save()
    return render(request, "public/message.html", _common({"title": "تم تأكيد اشتراكك", "text": "ستصلك النشرة اليومية كل صباح."}))


@csrf_exempt
def newsletter_unsubscribe(request, token: str):
    sub = get_object_or_404(NewsletterSubscriber, token=token)
    if request.method == "POST":
        sub.unsubscribed_at = timezone.now()
        sub.save()
        return render(request, "public/message.html", _common({"title": "أُلغي اشتراكك", "text": "لن تصلك النشرة بعد الآن."}))
    return render(request, "public/unsubscribe.html", _common({"sub": sub}))


@csrf_exempt
@require_POST
def push_subscribe(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
        endpoint = data["endpoint"]
        keys = data["keys"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    from arcms.distribution.channels import push_endpoint_allowed, push_keys_valid

    if not isinstance(keys, dict) or not push_endpoint_allowed(str(endpoint)) or not push_keys_valid(
        keys.get("p256dh"), keys.get("auth")
    ):
        return JsonResponse({"ok": False}, status=400)
    if _limited("push", client_ip(request)):
        return JsonResponse({"ok": False}, status=429)
    PushSubscription.objects.update_or_create(
        endpoint=endpoint[:700], defaults={"p256dh": keys["p256dh"], "auth": keys["auth"], "is_active": True}
    )
    return JsonResponse({"ok": True})


@csrf_exempt
@require_POST
def push_unsubscribe(request):
    try:
        endpoint = json.loads(request.body.decode("utf-8"))["endpoint"]
    except (ValueError, KeyError, TypeError):
        return JsonResponse({"ok": False}, status=400)
    PushSubscription.objects.filter(endpoint=endpoint).update(is_active=False)
    return JsonResponse({"ok": True})


# --- القياس ---


@csrf_exempt
@require_POST
def beacon(request):
    site = SiteSettings.load()
    if not site.analytics_enabled:
        return HttpResponse(status=204)
    if site.analytics_respect_dnt and (request.META.get("HTTP_DNT") == "1" or request.META.get("HTTP_SEC_GPC") == "1"):
        return HttpResponse(status=204)
    if len(request.body) > 2048:
        return HttpResponse(status=413)
    if _limited("beacon", client_ip(request)):
        return HttpResponse(status=204)  # لا يُحتسب ما زاد على المعقول من المصدر نفسه
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except ValueError:
        return HttpResponse(status=400)

    def as_int(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    article_id = as_int(data.get("a"))
    if article_id and not Article.objects.published().filter(pk=article_id).exists():
        article_id = None  # لا تُحتسب مشاهدة لمسودة، ولا يظهر عنوانها في لوحة الجمهور
    record_view(
        ip=client_ip(request),
        user_agent=request.META.get("HTTP_USER_AGENT", ""),
        path=str(data.get("p", ""))[:300],
        referrer=str(data.get("r", ""))[:500],
        article_id=article_id,
        category_id=as_int(data.get("c")),
        utm_source=str(data.get("u", ""))[:40],
    )
    return HttpResponse(status=204)


# --- ملفات خاصة ---


def favicon(request):
    from django.templatetags.static import static

    site = SiteSettings.load()
    return redirect(site.logo.thumb if site.logo_id else static("img/icon-192.png"))


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Disallow: /studio/",
        "Disallow: /accounts/",
        "Disallow: /search",
        f"Sitemap: {absolute_url('/sitemap.xml')}",
        f"Sitemap: {absolute_url('/news-sitemap.xml')}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain; charset=utf-8")


def web_manifest(request):
    site = SiteSettings.load()
    icon = site.logo.thumb if site.logo_id else "/static/img/icon-192.png"
    data = {
        "name": site.name,
        "short_name": site.short_name,
        "lang": "ar",
        "dir": "rtl",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#ffffff",
        "theme_color": site.primary_color,
        "icons": [
            {"src": icon, "sizes": "192x192", "type": "image/png"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return JsonResponse(data, json_dumps_params={"ensure_ascii": False}, content_type="application/manifest+json")


def service_worker(request):
    path = settings.BASE_DIR / "static" / "js" / "sw.js"
    response = HttpResponse(path.read_text(encoding="utf-8"), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


def push_config(request):
    site = SiteSettings.load()
    from arcms.distribution.models import Channel, ChannelConfig

    enabled = bool(site.push_enabled and settings.ARCMS_VAPID_PUBLIC_KEY and ChannelConfig.get(Channel.PUSH).enabled)
    return JsonResponse({"enabled": enabled, "key": settings.ARCMS_VAPID_PUBLIC_KEY if enabled else ""})


def breaking_json(request):
    return JsonResponse(
        {"items": [{"id": b.pk, "text": b.text, "url": b.get_url(), "at": b.created_at.isoformat()} for b in ticker()]},
        json_dumps_params={"ensure_ascii": False},
    )


def not_found(request, exception=None):
    return render(request, "public/404.html", _common({"latest": list(published()[:6])}), status=404)


def server_error(request):
    # قالب مستقل لا يلمس قاعدة البيانات، فيعمل حتى لو كانت القاعدة متوقفة.
    from django.template import loader

    return HttpResponse(loader.get_template("public/500.html").render({}), status=500)
