"""خطة التغطية: لوحة المهام بمراحلها، والتكليف، وبدء المادة من المهمة."""

from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.content.models import Article, EditorialNote, Status
from arcms.core.jobs import enqueue
from arcms.planning.models import Assignment

from .base import requires
from .forms import AssignmentForm

COLUMNS = (Assignment.Status.IDEA, Assignment.Status.ASSIGNED, Assignment.Status.WORKING, Assignment.Status.FILED,
           Assignment.Status.DONE)


def visible(user):
    """ما يراه المستخدم: المخطِّط يرى أقسامه (أو كل شيء بلا أقسام)، والكاتب مهامه فقط."""
    qs = Assignment.objects.select_related("assignee", "category", "article", "created_by")
    if not user.can(Cap.PLANNING):
        return qs.filter(assignee=user)
    desks = user.desk_ids()
    if desks:
        qs = qs.filter(Q(category_id__in=desks) | Q(category__isnull=True) | Q(assignee=user) | Q(created_by=user))
    return qs


@requires(Cap.PLANNING, Cap.ARTICLE_CREATE)
def board(request):
    user = request.user
    qs = visible(user)
    who = request.GET.get("who", "")
    if who == "mine":
        qs = qs.filter(assignee=user)
    elif who.isdigit() and user.can(Cap.PLANNING):
        qs = qs.filter(assignee_id=int(who))
    week = timezone.now() - timedelta(days=7)
    labels = dict(Assignment.Status.choices)
    columns = []
    for status in COLUMNS:
        items = qs.filter(status=status)
        if status == Assignment.Status.DONE:
            items = items.filter(updated_at__gte=week).order_by("-updated_at")
        columns.append({"status": status, "label": labels[status], "items": list(items[:60])})
    team = []
    if user.can(Cap.PLANNING):
        from arcms.accounts.models import User

        team = User.objects.filter(assignments__status__in=Assignment.OPEN).distinct().order_by("display_name")
    return render(request, "studio/planning.html", {
        "columns": columns, "who": who, "team": team, "planner": user.can(Cap.PLANNING),
        "dropped": qs.filter(status=Assignment.Status.DROPPED).order_by("-updated_at")[:20] if request.GET.get("dropped") else None,
    })


def _notify(assignment: Assignment, actor) -> None:
    """بريد للمكلَّف دون عنوان الموضوع (خطة التغطية لا تغادر الخادم)، كسائر تنبيهات الطاقم."""
    target = assignment.assignee
    if not target or target == actor or not target.email or not target.email_notifications:
        return
    from django.urls import reverse

    from arcms.arabic.dates import format_datetime
    from arcms.core.utils import absolute_url

    due = f"، موعدها {format_datetime(assignment.due_at)}" if assignment.due_at else ""
    body = f"كلّفك {actor} بمهمة تغطية (رقم {assignment.pk}){due}.\n\n{absolute_url(reverse('studio:planning'))}\n"
    enqueue("staff.email", {"to": target.email, "subject": "كُلّفت بمهمة تغطية جديدة", "body": body}, max_attempts=4)


@requires(Cap.PLANNING)
def edit(request, pk: int | None = None):
    assignment = get_object_or_404(visible(request.user), pk=pk) if pk else None
    before = assignment.assignee_id if assignment else None
    form = AssignmentForm(request.POST or None, instance=assignment)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if not obj.pk:
            obj.created_by = request.user
        obj.save()
        if obj.assignee_id and obj.assignee_id != before:
            _notify(obj, request.user)
        messages.success(request, "حُفظت المهمة.")
        return redirect("studio:planning")
    return render(request, "studio/assignment_form.html", {"form": form, "obj": assignment})


@requires(Cap.ARTICLE_CREATE)
@require_POST
def start(request, pk: int):
    """يبدأ المكلَّف مادته من المهمة: مسودة بالعنوان والقسم، والتوجيه ملاحظةً تحريرية فيها."""
    user = request.user
    assignment = get_object_or_404(visible(user), pk=pk)
    if assignment.article_id:
        return redirect("studio:article_edit", assignment.article_id)
    if assignment.assignee_id != user.pk:
        if not (user.can(Cap.PLANNING) and assignment.assignee_id is None):
            raise PermissionDenied("المهمة مكلَّف بها زميل آخر.")
        assignment.assignee = user
    if assignment.status == Assignment.Status.DROPPED:
        raise PermissionDenied("المهمة ملغاة.")
    article = Article.objects.create(
        title=assignment.title[:250], kind=assignment.kind or "news", category=assignment.category,
        status=Status.DRAFT, created_by=user, last_edited_by=user,
    )
    if assignment.brief.strip():
        EditorialNote.objects.create(article=article, author=assignment.created_by or user, kind="comment",
                                     body=f"توجيه المهمة: {assignment.brief.strip()}")
    assignment.article, assignment.status = article, Assignment.Status.WORKING
    assignment.save()
    messages.success(request, "بدأت المادة من المهمة. التوجيه في «ملاحظات التحرير».")
    return redirect("studio:article_edit", article.pk)


@requires(Cap.PLANNING)
@require_POST
def action(request, pk: int):
    assignment = get_object_or_404(visible(request.user), pk=pk)
    what = request.POST.get("action")
    if what == "drop" and assignment.status in Assignment.OPEN:
        assignment.status = Assignment.Status.DROPPED
        assignment.save()
        messages.success(request, "أُلغيت المهمة.")
    elif what == "restore" and assignment.status == Assignment.Status.DROPPED:
        if assignment.article_id:
            assignment.status = Assignment.status_for_article(assignment.article)
        else:
            assignment.status = Assignment.Status.ASSIGNED if assignment.assignee_id else Assignment.Status.IDEA
        assignment.save()
        messages.success(request, "أُعيدت المهمة.")
    return redirect("studio:planning")
