from arcms.audit.models import Action
from arcms.audit.services import record
from arcms.core.jobs import periodic

from .services import purge_expired, retention_days


@periodic("tips_purge", 6 * 3600)
def tips_purge() -> None:
    n = purge_expired()
    if n:
        record(Action.DELETE, message=f"حذف تلقائي لـ {n} بلاغاً مغلقاً مضى عليه أكثر من {retention_days()} يوماً",
               object_type="tips.tip")
