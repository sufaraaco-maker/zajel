"""عروض المواد: لوحة البداية، القائمة، المحرر، سير العمل، المراجعات."""

from __future__ import annotations

import difflib
import json
from datetime import datetime, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap, Role
from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.content import search as search_mod
from arcms.content.models import (
    Article,
    ArticleKind,
    ArticleLock,
    ArticleRevision,
    Author,
    BreakingNews,
    Category,
    EditorialNote,
    LiveCoverage,
    Status,
    Tag,
)
from arcms.content.workflow import (
    WorkflowError,
    available_actions,
    can_edit,
    can_view,
    can_view_sources,
    in_scope,
    publish_block_reason,
    transition,
)
from arcms.core.models import Job, WorkerHeartbeat

from .base import paginate, requires
from .forms import ArticleForm

WRITE_CAPS = (Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY, Cap.ARTICLE_PUBLISH, Cap.DIST_SEND, Cap.ANALYTICS)


@requires()
def home(request):
    user = request.user
    mine = Article.objects.filter(created_by=user).select_related("category")
    ctx = {
        "greeting": "صباح الخير" if timezone.localtime().hour < 12 else "مساء الخير",
        "my_drafts": mine.filter(status__in=[Status.DRAFT, Status.CHANGES]).order_by("-updated_at")[:8],
        "returned_count": mine.filter(status=Status.CHANGES).count(),
        "my_recent": mine.filter(status=Status.PUBLISHED).order_by("-published_at")[:5],
        "my_tasks": _my_tasks(user),
    }
    if user.can(Cap.ARTICLE_REVIEW):
        review = Article.objects.filter(status=Status.IN_REVIEW).exclude(created_by=user).select_related("category", "created_by")
        desks = user.desk_ids()
        if desks:
            review = review.filter(Q(category_id__in=desks) | Q(category__isnull=True))
        ctx["review_queue"] = review.order_by("submitted_at")[:10]
        ctx["review_count"] = review.count()
    if user.can(Cap.ARTICLE_PUBLISH):
        ctx["approved"] = Article.objects.filter(status=Status.APPROVED).select_related("category").order_by("-updated_at")[:8]
        ctx["scheduled"] = Article.objects.filter(status=Status.SCHEDULED).order_by("scheduled_at")[:8]
    ctx["recent_published"] = Article.objects.published().select_related("category", "published_by").order_by("-published_at")[:8]
    ctx["live_now"] = LiveCoverage.objects.filter(is_live=True)[:3]
    ctx["breaking"] = BreakingNews.current(12)[:5]
    if user.can(Cap.ANALYTICS):
        from arcms.analytics import queries

        ctx["today"] = queries.today_numbers()
        ctx["realtime"] = queries.realtime()
    ctx["worker_ok"] = WorkerHeartbeat.healthy()
    ctx["failed_jobs"] = Job.objects.filter(status=Job.Status.FAILED, finished_at__gte=timezone.now() - timedelta(days=2)).count()
    return render(request, "studio/home.html", ctx)


def _my_tasks(user):
    from arcms.planning.models import Assignment

    return list(Assignment.objects.filter(assignee=user, status__in=Assignment.OPEN)
                .select_related("category", "article").order_by("due_at", "-priority")[:6])


@requires(*WRITE_CAPS)
def article_list(request):
    user = request.user
    qs = Article.objects.select_related("category", "created_by", "featured_image")
    if not (user.can(Cap.ARTICLE_EDIT_ANY) or user.can(Cap.ARTICLE_PUBLISH)):
        if user.can(Cap.DIST_SEND):
            qs = qs.filter(Q(created_by=user) | Q(status=Status.PUBLISHED))
        else:
            qs = qs.filter(created_by=user)
    status = request.GET.get("status", "")
    kind = request.GET.get("kind", "")
    category = request.GET.get("category", "")
    mine = request.GET.get("mine", "")
    q = request.GET.get("q", "").strip()
    if status in Status.values:
        qs = qs.filter(status=status)
    if kind in ArticleKind.values:
        qs = qs.filter(kind=kind)
    if category.isdigit():
        qs = qs.filter(category_id=int(category))
    if mine:
        qs = qs.filter(created_by=user)
    if q:
        if q.isdigit():
            qs = qs.filter(pk=int(q))
            page = paginate(request, qs.order_by("-updated_at"))
        else:
            result = search_mod.search_articles(q, queryset=qs, public=False, limit=100)
            page = None
            items = result.articles
    else:
        page = paginate(request, qs.order_by("-updated_at"))
    counts = dict(Article.objects.values_list("status").annotate(n=Count("id")))
    ctx = {
        "page": page,
        "items": page.object_list if page is not None else items,
        "statuses": Status.choices,
        "kinds": ArticleKind.choices,
        "categories": Category.objects.filter(is_active=True),
        "counts": counts,
        "f": {"status": status, "kind": kind, "category": category, "q": q, "mine": mine},
    }
    return render(request, "studio/articles.html", ctx)


def _lock(article: Article, user) -> tuple[bool, object]:
    """يحجز المادة للمستخدم. يعيد (نجح؟، صاحب القفل الحالي)."""
    with transaction.atomic():
        lock = ArticleLock.objects.select_for_update().filter(article=article).first()
        if lock and lock.user_id != user.pk and not lock.is_stale:
            return False, lock.user
        ArticleLock.objects.update_or_create(article=article, defaults={"user": user, "heartbeat_at": timezone.now()})
    return True, user


def _parse_when(value: str) -> datetime | None:
    if not value:
        return None
    try:
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return timezone.make_aware(naive)


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY)
def article_edit(request, pk: int | None = None):
    user = request.user
    article = get_object_or_404(Article, pk=pk) if pk else None
    if article is None and not user.can(Cap.ARTICLE_CREATE):
        raise PermissionDenied
    if article is not None and not can_view(user, article):
        raise PermissionDenied
    editable = article is None or can_edit(user, article)
    locked_by = None
    if article is not None and editable:
        ok, holder = _lock(article, user)
        if not ok and request.GET.get("takeover") and user.can(Cap.ARTICLE_PUBLISH):
            ArticleLock.objects.filter(article=article).update(user=user, heartbeat_at=timezone.now())
            record(Action.SECURITY, article, message=f"انتزاع قفل التحرير من {holder}")
            ok = True
        if not ok:
            editable, locked_by = False, holder

    initial = {}
    if article is None:
        kind = request.GET.get("kind")
        if kind in ArticleKind.values:
            initial["kind"] = kind
        author = Author.objects.filter(user=user).first()
        if author:
            initial["authors"] = [author]
        if kind == ArticleKind.VIDEO:
            initial.update(send_telegram=True)
    form = ArticleForm(request.POST or None, request.FILES or None, instance=article, user=user, initial=initial)
    if not editable:
        for field in form.fields.values():
            field.disabled = True

    if request.method == "POST" and editable:
        action = request.POST.get("action", "save")
        before = _content_fingerprint(article) if article is not None else None
        if form.is_valid():
            with transaction.atomic():
                obj = form.save(commit=False)
                creating = obj.pk is None
                if creating:
                    obj.created_by = user
                obj.last_edited_by = user
                if obj.status == Status.PUBLISHED and not creating:
                    obj.content_updated_at = timezone.now()
                if "correction" in form.changed_data:
                    # التصحيح يُؤرَّخ ليظهر في «سجل التصحيحات» العام؛ حذفه لا يمحو أثره من سجل النسخ
                    obj.corrected_at = timezone.now() if obj.correction.strip() else None
                if _needs_new_review(obj, user, before):
                    # «العينان الأربع»: ما اعتمده الزميل هو ما يُنشر. تعديل الكاتب بعد الاعتماد يعيدها للمراجعة.
                    obj.status, obj.reviewed_by, obj.scheduled_at = Status.IN_REVIEW, None, None
                    messages.warning(request, "عدّلت المادة بعد اعتمادها، فعادت إلى المراجعة.")
                obj.save()
                form.save_m2m()
                form.save_tags(obj)
                if obj.category and not in_scope(user, obj):
                    raise PermissionDenied("القسم المختار خارج أقسامك.")
                ArticleRevision.capture(obj, user, note="حفظ" if not creating else "إنشاء")
            if action != "save":
                try:
                    obj = transition(
                        obj, user, action,
                        note=request.POST.get("note", ""),
                        when=_parse_when(request.POST.get("scheduled_for", "")),
                    )
                    messages.success(request, f"تم: {obj.get_status_display()}.")
                except WorkflowError as exc:
                    messages.error(request, str(exc))
            else:
                messages.success(request, "حُفظت المادة.")
            removed, warning = getattr(form, "audio_report", ([], ""))
            if removed:
                messages.info(request, "نُزع من الملف الصوتي: " + "، ".join(removed) + ".")
            if warning:
                messages.warning(request, warning)
            return redirect("studio:article_edit", pk=obj.pk)
        messages.error(request, "راجع الحقول المعلَّمة.")

    ctx = {
        "stats": _article_stats(article) if article is not None and article.first_published_at else None,
        "form": form,
        "article": article,
        "editable": editable,
        "locked_by": locked_by,
        "actions": available_actions(user, article) if article else [],
        "block_reason": publish_block_reason(user, article) if article else None,
        "notes": article.notes.select_related("author") if article else [],
        "revisions": article.revisions.select_related("created_by")[:12] if article else [],
        "deliveries": article.deliveries.all()[:12] if article else [],
        "can_sources": article is None or can_view_sources(user, article),
        "kinds": ArticleKind.choices,
        "now_local": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        **_card_context(),
    }
    return render(request, "studio/article_edit.html", ctx)


def _card_context() -> dict:
    from arcms.content import cards
    from arcms.core.models import SiteSettings

    return {"cards_on": SiteSettings.load().share_cards and cards.available(),
            "card_formats": [(key, short, hint) for key, (short, hint) in cards.FORMAT_LABELS.items()]}


_REVIEWED_FIELDS = ("kicker", "title", "subtitle", "excerpt", "dateline", "body", "category_id", "featured_image_id",
                    "image_caption", "video_url", "correction")


def _content_fingerprint(article: Article) -> tuple:
    return tuple(getattr(article, f) for f in _REVIEWED_FIELDS)


def _needs_new_review(obj: Article, user, before: tuple | None) -> bool:
    from arcms.core.models import SiteSettings

    if before is None or obj.status not in (Status.APPROVED, Status.SCHEDULED) or obj.created_by_id != user.pk:
        return False
    if user.role in (Role.CHIEF, Role.ADMIN) or user.is_superuser or not SiteSettings.load().require_review:
        return False
    return _content_fingerprint(obj) != before


def _article_stats(article: Article) -> dict:
    """أرقام مادة منشورة: الإجمالي، اليوم، آخر 7 أيام، ومصادر الزيارات."""
    from arcms.analytics.models import PageView

    start_today = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    week = PageView.objects.filter(article_id=article.pk, ts__gte=timezone.now() - timedelta(days=7))
    labels = dict(PageView.Source.choices)
    sources = list(week.values("source").annotate(n=Count("id")).order_by("-n")[:5])
    week_total = sum(r["n"] for r in sources) or 1
    return {
        "total": article.view_count,
        "today": PageView.objects.filter(article_id=article.pk, ts__gte=start_today).count(),
        "week": week.count(),
        "sources": [(labels.get(r["source"], r["source"]), r["n"], round(100 * r["n"] / week_total)) for r in sources],
    }


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY)
@require_POST
def article_heartbeat(request, pk: int):
    article = get_object_or_404(Article, pk=pk)
    lock = ArticleLock.objects.filter(article=article).first()
    if lock and lock.user_id == request.user.pk:
        ArticleLock.objects.filter(pk=lock.pk).update(heartbeat_at=timezone.now())
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": False, "holder": str(lock.user) if lock else ""})


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY)
@require_POST
def article_autosave(request, pk: int):
    """حفظ تلقائي للمسودات فقط؛ لا يغيّر مادة منشورة دون ضغط «حفظ»."""
    article = get_object_or_404(Article, pk=pk)
    if not can_edit(request.user, article) or article.status not in (Status.DRAFT, Status.CHANGES):
        return JsonResponse({"ok": False})
    lock = ArticleLock.objects.filter(article=article).first()
    if lock and lock.user_id != request.user.pk and not lock.is_stale:
        return JsonResponse({"ok": False, "holder": str(lock.user)})
    try:
        data = json.loads(request.body.decode("utf-8"))
    except ValueError:
        return JsonResponse({"ok": False}, status=400)
    from arcms.content.sanitize import sanitize_html

    changed = []
    for field in ("title", "subtitle", "excerpt", "kicker", "dateline"):
        if field in data and isinstance(data[field], str):
            setattr(article, field, data[field][: Article._meta.get_field(field).max_length or 5000])
            changed.append(field)
    if isinstance(data.get("body"), str):
        article.body = sanitize_html(data["body"])
        changed.append("body")
    if changed and article.title.strip():
        article.save(update_fields=[*changed, "updated_at", "word_count", "slug"])
    return JsonResponse({"ok": True, "saved_at": timezone.localtime().strftime("%H:%M")})


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY, Cap.ARTICLE_REVIEW)
@require_POST
def article_note(request, pk: int):
    article = get_object_or_404(Article, pk=pk)
    if not can_view(request.user, article):
        raise PermissionDenied
    body = request.POST.get("body", "").strip()
    if body:
        EditorialNote.objects.create(article=article, author=request.user, body=body[:3000])
    return redirect(reverse("studio:article_edit", args=[pk]) + "#notes")


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY)
@require_POST
def article_delete(request, pk: int):
    article = get_object_or_404(Article, pk=pk)
    own_draft = article.created_by_id == request.user.pk and article.status == Status.DRAFT and not article.first_published_at
    if not (own_draft or request.user.can(Cap.ARTICLE_DELETE)):
        raise PermissionDenied
    title = article.title
    article.delete()
    messages.success(request, f"حُذفت «{title}».")
    return redirect("studio:articles")


@requires(Cap.DIST_SEND)
@require_POST
def article_distribute(request, pk: int):
    from arcms.distribution.dispatch import on_article_published

    article = get_object_or_404(Article, pk=pk)
    channels = set(request.POST.getlist("channel"))
    created = on_article_published(article.pk, force=True, only=channels or None)
    if created:
        messages.success(request, f"أُضيفت {len(created)} عملية إرسال إلى الطابور.")
    else:
        messages.warning(request, "لم يُرسل شيء: تحقق من تفعيل القنوات في إعدادات التوزيع.")
    return redirect(reverse("studio:article_edit", args=[pk]) + "#distribution")


@requires(Cap.ARTICLE_CREATE, Cap.ARTICLE_EDIT_ANY)
def article_revision(request, pk: int, rev: int):
    article = get_object_or_404(Article, pk=pk)
    if not can_view(request.user, article):
        raise PermissionDenied
    revision = get_object_or_404(ArticleRevision, pk=rev, article=article)
    current = {f: getattr(article, f) for f in ArticleRevision.SNAPSHOT_FIELDS}
    diffs = []
    for field in ArticleRevision.SNAPSHOT_FIELDS:
        old = strip_tags(revision.data.get(field) or "")
        new = strip_tags(current.get(field) or "")
        if old == new:
            continue
        diffs.append({"field": Article._meta.get_field(field).verbose_name, "html": _word_diff(old, new)})
    if request.method == "POST":
        if not can_edit(request.user, article):
            raise PermissionDenied
        for field in ArticleRevision.SNAPSHOT_FIELDS:
            if field in revision.data:
                setattr(article, field, revision.data[field])
        article.last_edited_by = request.user
        article.save()
        ArticleRevision.capture(article, request.user, note=f"استرجاع نسخة {timezone.localtime(revision.created_at):%Y-%m-%d %H:%M}")
        messages.success(request, "استُرجعت النسخة.")
        return redirect("studio:article_edit", pk=pk)
    return render(request, "studio/revision.html", {"article": article, "revision": revision, "diffs": diffs})


def _word_diff(old: str, new: str) -> str:
    from html import escape

    a, b = old.split(), new.split()
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(escape(" ".join(a[i1:i2])))
        if op in ("delete", "replace"):
            out.append(f"<del>{escape(' '.join(a[i1:i2]))}</del>")
        if op in ("insert", "replace"):
            out.append(f"<ins>{escape(' '.join(b[j1:j2]))}</ins>")
    return " ".join(out)


# --- واجهات JSON للمحرر ---


@requires(*WRITE_CAPS)
def api_tags(request):
    from arcms.arabic.normalize import normalize

    q = normalize(request.GET.get("q", "").strip())
    qs = Tag.objects.all()
    if q:
        qs = qs.filter(normalized__contains=q)
    tags = qs.annotate(n=Count("articles")).order_by("-n")[:12]
    return JsonResponse({"items": [{"id": t.pk, "name": t.name, "count": t.n} for t in tags]})


@requires(*WRITE_CAPS)
def api_articles(request):
    q = request.GET.get("q", "").strip()
    if q:
        items = search_mod.search_articles(q, public=True, limit=10).articles
    else:
        items = list(Article.objects.published().order_by("-published_at")[:10])
    return JsonResponse(
        {"items": [{"id": a.pk, "title": a.title, "date": timezone.localtime(a.published_at).strftime("%Y-%m-%d") if a.published_at else ""} for a in items]}
    )
