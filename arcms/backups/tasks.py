from django.conf import settings
from django.utils import timezone

from arcms.core.jobs import enqueue, handler, periodic
from config import env


@periodic("backup_schedule", 600)
def schedule_backup():
    hour = env.get_int("ARCMS_BACKUP_HOUR", 3)
    if hour < 0 or not settings.ARCMS_BACKUP_PUBLIC_KEY:
        return
    now = timezone.localtime()
    if now.hour >= hour:
        enqueue("backup.run", {"trigger": "scheduled"}, dedupe_key=f"backup:{now.date().isoformat()}", max_attempts=2)


@handler("backup.run")
def run_backup(payload):
    from .service import create_backup

    rec = create_backup(trigger=payload.get("trigger", "scheduled"), include_media=payload.get("media", True))
    return {"file": rec.filename, "size": rec.size}
