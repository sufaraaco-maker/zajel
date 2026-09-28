"""تنبيهات البريد الداخلية للطاقم عند محطات سير العمل وعند وصول بلاغ.

البريد يخرج من خادم المؤسسة إلى مزوّدي البريد، لذلك لا يحمل إلا الحد
الأدنى: رقم المادة ورابطها في غرفة التحرير واسم من قام بالإجراء. عنوان
المادة لا يُذكر إلا بعد نشرها، ولا يُذكر من البلاغات شيء على الإطلاق.
"""

from __future__ import annotations

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from django.urls import reverse

from arcms.core.jobs import enqueue, handler
from arcms.core.utils import absolute_url

from .roles import ROLE_CAPABILITIES, Cap


def _with_cap(cap: str):
    from .models import User

    roles = [r for r, caps in ROLE_CAPABILITIES.items() if cap in caps]
    return (
        User.objects.filter(Q(role__in=roles) | Q(is_superuser=True), is_active=True, email_notifications=True)
        .exclude(email="")
    )


def _in_scope(user, article) -> bool:
    desks = user.desk_ids()
    return not desks or article.category_id in desks


def _queue(users, subject: str, body: str, exclude: set[int]) -> int:
    n = 0
    for user in users:
        if user.pk in exclude or not user.email:
            continue
        enqueue("staff.email", {"to": user.email, "subject": subject, "body": body}, max_attempts=4)
        n += 1
    return n


def workflow_event(article_id: int, action: str, actor_id: int | None) -> int:
    from arcms.content.models import Article

    article = Article.objects.select_related("created_by").filter(pk=article_id).first()
    if article is None:
        return 0
    from .models import User

    actor = User.objects.filter(pk=actor_id).first() if actor_id else None
    who = str(actor) if actor else "النشر المجدول"
    link = absolute_url(reverse("studio:article_edit", args=[article.pk]))
    ref = f"المادة رقم {article.pk}"
    author = article.created_by if article.created_by and article.created_by.email_notifications else None
    skip = {actor_id} if actor_id else set()

    if action == "submit":
        reviewers = [u for u in _with_cap(Cap.ARTICLE_REVIEW) if _in_scope(u, article)]
        return _queue(reviewers, f"{ref} بانتظار المراجعة", f"أرسل {who} {ref} للمراجعة.\n\n{link}\n", skip | {article.created_by_id})
    if action == "return" and author:
        return _queue([author], f"أُعيدت {ref} للتعديل", f"أعاد {who} {ref} إليك مع ملاحظات في غرفة التحرير.\n\n{link}\n", skip)
    if action == "approve":
        publishers = [u for u in _with_cap(Cap.ARTICLE_PUBLISH) if _in_scope(u, article)]
        n = _queue(publishers, f"{ref} معتمدة وجاهزة للنشر", f"اعتمد {who} {ref}.\n\n{link}\n", skip | {article.created_by_id})
        if author:
            n += _queue([author], f"اعتُمدت {ref}", f"اعتمد {who} مادتك ({ref}).\n\n{link}\n", skip)
        return n
    if action == "publish" and author:
        public = absolute_url(article.get_absolute_url())
        return _queue([author], f"نُشرت مادتك: {article.title}", f"نُشرت مادتك «{article.title}».\n\n{public}\n", skip)
    return 0


def new_tip() -> int:
    link = absolute_url(reverse("studio:tips"))
    return _queue(
        _with_cap(Cap.TIPS), "وصلت رسالة جديدة إلى صندوق المعلومات", f"افتح صندوق المعلومات في غرفة التحرير:\n{link}\n", set()
    )


@handler("staff.email")
def send_staff_email(payload: dict) -> None:
    send_mail(payload["subject"], payload["body"] + "\n—\nتنبيه آلي من غرفة التحرير. يمكنك إيقافه من صفحة «حسابي».",
              settings.DEFAULT_FROM_EMAIL, [payload["to"]])
