"""دورة المادة: مسودة ← مراجعة ← اعتماد ← نشر (فوري أو مجدول) ← سحب.

كل انتقال يمرّ بدالة واحدة تتحقق من رتبة المستخدم ونطاق أقسامه،
وتسجّل ملاحظة تحريرية وقيداً في سجل التدقيق، وتطلق التوزيع عند النشر.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from arcms.accounts.roles import Cap, Role
from arcms.audit.models import Action
from arcms.audit.services import record

from .models import Article, ArticleKind, ArticleRevision, EditorialNote, Status


class WorkflowError(Exception):
    pass


# --- الصلاحيات على مادة بعينها ---


def in_scope(user, article: Article) -> bool:
    """هل المادة ضمن أقسام المستخدم؟ (من لا أقسام محددة له يعمل على الكل)."""
    desks = user.desk_ids()
    if not desks:
        return True
    return article.category_id is None or article.category_id in desks


def can_view(user, article: Article) -> bool:
    if not user.is_authenticated:
        return False
    if article.created_by_id == user.pk:
        return True
    if (user.can(Cap.ARTICLE_EDIT_ANY) or user.can(Cap.ARTICLE_PUBLISH)) and in_scope(user, article):
        return True
    return user.can(Cap.DIST_SEND) and article.status == Status.PUBLISHED


def can_edit(user, article: Article) -> bool:
    # الكاتب يعدّل مادته وهي مسودة أو معادة إليه؛ أثناء المراجعة وبعد النشر تصبح بيد المحررين.
    if (
        article.created_by_id == user.pk
        and user.can(Cap.ARTICLE_EDIT_OWN)
        and article.status in (Status.DRAFT, Status.CHANGES)
    ):
        return True
    return user.can(Cap.ARTICLE_EDIT_ANY) and in_scope(user, article)


def can_view_sources(user, article: Article) -> bool:
    return article.created_by_id == user.pk or user.can(Cap.SOURCES_VIEW)


def can_publish(user, article: Article) -> bool:
    return user.can(Cap.ARTICLE_PUBLISH) and in_scope(user, article)


def publish_block_reason(user, article: Article) -> str | None:
    """قاعدة «العينين الأربع»: لا ينشر أحد مادته وحده إلا رئيس التحرير."""
    from arcms.core.models import SiteSettings

    if not can_publish(user, article):
        return "لا تملك صلاحية النشر في هذا القسم."
    if not article.title.strip():
        return "لا يمكن نشر مادة بلا عنوان."
    if article.category_id is None:
        return "اختر قسماً للمادة قبل النشر."
    if article.kind == ArticleKind.FACTCHECK and not (article.claim.strip() and article.verdict):
        return "مادة التدقيق تحتاج نص الادعاء والحكم عليه قبل النشر."
    if not SiteSettings.load().require_review:
        return None
    if user.role in (Role.CHIEF, Role.ADMIN) or user.is_superuser:
        return None
    if article.created_by_id != user.pk:
        return None
    if article.status == Status.APPROVED and article.reviewed_by_id and article.reviewed_by_id != user.pk:
        return None
    if article.first_published_at:
        return None  # تعديل على مادة سبق نشرها
    return "سياسة المؤسسة تشترط مراجعة زميل قبل نشر مادتك. أرسلها للمراجعة."


def available_actions(user, article: Article) -> list[tuple[str, str]]:
    """الأزرار المتاحة في المحرر لهذا المستخدم وهذه الحالة."""
    s = article.status
    actions: list[tuple[str, str]] = []
    owner = article.created_by_id == user.pk
    if s in (Status.DRAFT, Status.CHANGES) and (owner or can_edit(user, article)) and user.can(Cap.ARTICLE_SUBMIT):
        actions.append(("submit", "إرسال للمراجعة"))
    if s == Status.IN_REVIEW and user.can(Cap.ARTICLE_REVIEW) and in_scope(user, article) and not owner:
        actions += [("approve", "اعتماد"), ("return", "إعادة للكاتب")]
    if s == Status.IN_REVIEW and owner:
        actions.append(("withdraw", "سحب من المراجعة"))
    if s != Status.PUBLISHED and publish_block_reason(user, article) is None:
        actions += [("publish", "نشر الآن"), ("schedule", "جدولة")]
    if s == Status.SCHEDULED and can_publish(user, article):
        actions.append(("unschedule", "إلغاء الجدولة"))
    if s == Status.PUBLISHED and user.can(Cap.ARTICLE_UNPUBLISH) and in_scope(user, article):
        actions.append(("unpublish", "سحب من الموقع"))
    return actions


# --- الانتقالات ---


def _note(article: Article, user, body: str, kind: str) -> None:
    if body:
        EditorialNote.objects.create(article=article, author=user, body=body, kind=kind)


def _log(article: Article, action: str, message: str, before: str) -> None:
    record(
        Action.PUBLISH if action == "publish" else Action.UNPUBLISH if action == "unpublish" else Action.WORKFLOW,
        article,
        message=message,
        changes={"status": [before, article.status]},
    )


@transaction.atomic
def transition(article: Article, user, action: str, *, note: str = "", when: datetime | None = None) -> Article:
    article = Article.objects.select_for_update().get(pk=article.pk)
    allowed = dict(available_actions(user, article))
    if action not in allowed:
        reason = publish_block_reason(user, article) if action in ("publish", "schedule") else None
        raise WorkflowError(reason or "هذا الإجراء غير متاح لك في حالة المادة الحالية.")
    before = article.status
    now = timezone.now()

    if action == "submit":
        article.status = Status.IN_REVIEW
        article.submitted_at = now
        _note(article, user, note, "comment")
        message = "أُرسلت للمراجعة"
    elif action == "withdraw":
        article.status = Status.DRAFT
        message = "سُحبت من المراجعة"
    elif action == "approve":
        article.status = Status.APPROVED
        article.reviewed_by = user
        _note(article, user, note or "اعتُمدت للنشر.", "approve")
        message = "اعتُمدت"
    elif action == "return":
        if not note.strip():
            raise WorkflowError("اكتب للكاتب سبب الإعادة وما المطلوب تعديله.")
        article.status = Status.CHANGES
        article.reviewed_by = user
        _note(article, user, note, "return")
        message = "أُعيدت للتعديل"
    elif action == "publish":
        _mark_published(article, user, now)
        message = "نُشرت"
    elif action == "schedule":
        if not when or when <= now:
            raise WorkflowError("اختر موعداً في المستقبل للجدولة.")
        article.status = Status.SCHEDULED
        article.scheduled_at = when
        if article.created_by_id != user.pk and not article.reviewed_by_id:
            article.reviewed_by = user
        article.published_by = user
        message = f"جُدولت للنشر في {timezone.localtime(when):%Y-%m-%d %H:%M}"
    elif action == "unschedule":
        article.status = Status.APPROVED if article.reviewed_by_id else Status.DRAFT
        article.scheduled_at = None
        message = "أُلغيت الجدولة"
    elif action == "unpublish":
        article.status = Status.UNPUBLISHED
        _note(article, user, note, "system")
        message = "سُحبت من الموقع"
    else:  # pragma: no cover
        raise WorkflowError("إجراء غير معروف.")

    article.last_edited_by = user
    article.save()
    ArticleRevision.capture(article, user, note=message)
    _log(article, action, message, before)
    if action in ("submit", "return", "approve", "publish"):
        from arcms.accounts.notify import workflow_event

        transaction.on_commit(lambda: workflow_event(article.pk, action, user.pk))
    if action == "publish":
        transaction.on_commit(lambda: _after_publish(article.pk))
    if action == "unpublish":
        from .signals import invalidate_public_cache

        transaction.on_commit(invalidate_public_cache)
    return article


def _mark_published(article: Article, user, when) -> None:
    if article.created_by_id != getattr(user, "pk", None) and not article.reviewed_by_id and user is not None:
        article.reviewed_by = user
    article.status = Status.PUBLISHED
    article.published_at = when
    article.first_published_at = article.first_published_at or when
    article.content_updated_at = when
    article.scheduled_at = None
    if user is not None:
        article.published_by = user


def _after_publish(article_id: int) -> None:
    from arcms.distribution.dispatch import on_article_published

    from .signals import invalidate_public_cache

    invalidate_public_cache()
    on_article_published(article_id)


def _notify_published(article_id: int) -> None:
    from arcms.accounts.notify import workflow_event

    workflow_event(article_id, "publish", None)


def publish_due(now: datetime | None = None) -> list[int]:
    """يُستدعى من العامل الخلفي كل دورة: ينشر المواد التي حان موعدها."""
    from arcms.audit.services import acting_as

    now = now or timezone.now()
    published = []
    due = Article.objects.filter(status=Status.SCHEDULED, scheduled_at__lte=now).values_list("pk", flat=True)
    for pk in list(due):
        with transaction.atomic(), acting_as(label="النشر المجدول"):
            article = Article.objects.select_for_update().get(pk=pk)
            if article.status != Status.SCHEDULED:
                continue
            when = article.scheduled_at or now
            publisher = article.published_by
            _mark_published(article, None, when)
            article.published_by = publisher
            article.save()
            ArticleRevision.capture(article, publisher, note="نُشرت آلياً في موعدها")
            record(Action.PUBLISH, article, message="نشر مجدول", changes={"status": [Status.SCHEDULED, Status.PUBLISHED]})
            transaction.on_commit(lambda pk=pk: _after_publish(pk))
            transaction.on_commit(lambda pk=pk: _notify_published(pk))
            published.append(pk)
    return published
