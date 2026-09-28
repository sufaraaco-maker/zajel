"""إرسال مرساة سجل التدقيق يومياً إلى المدققين، ليُحفظ دليل سلامته خارج الخادم."""

from __future__ import annotations

from django.urls import reverse
from django.utils import timezone

from arcms.core.jobs import enqueue, periodic

from .models import AuditEntry
from .services import current_anchor


def send_anchor() -> int:
    from arcms.accounts.notify import _with_cap
    from arcms.accounts.roles import Cap
    from arcms.core.utils import absolute_url

    anchor = current_anchor()
    if not anchor:
        return 0
    today = timezone.localdate().isoformat()
    count = AuditEntry.objects.count()
    body = (
        f"مرساة سجل التدقيق ليوم {today}:\n\n{anchor}\n\n"
        f"عدد القيود: {count}\n\n"
        "احتفظ بهذه الرسالة. للتحقق لاحقاً من أن السجل لم يُعدَّل ولم يُحذف منه شيء قبل هذه النقطة، "
        f"الصق المرساة في صفحة السجل: {absolute_url(reverse('studio:audit'))}\n"
        "أو شغّل على الخادم: manage.py arcms_audit_verify --expect " + anchor.split(":")[0] + ":…\n"
    )
    sent = 0
    for user in _with_cap(Cap.AUDIT):
        enqueue(
            "staff.email",
            {"to": user.email, "subject": f"مرساة سجل التدقيق {today}", "body": body},
            dedupe_key=f"audit-anchor:{today}:{user.pk}",
        )
        sent += 1
    return sent


@periodic("audit_anchor", 3600)
def audit_anchor() -> None:
    if timezone.localtime().hour >= 6:  # مرة يومياً صباحاً؛ مفتاح عدم التكرار يمنع إعادة الإرسال
        send_anchor()
