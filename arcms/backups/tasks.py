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
    from . import offsite

    if offsite.configured():
        rec.offsite_status = "pending"
        rec.save(update_fields=["offsite_status"])
        enqueue("backup.offsite", {"record": rec.pk}, dedupe_key=f"offsite:{rec.pk}", max_attempts=5)
    return {"file": rec.filename, "size": rec.size}


@handler("backup.offsite")
def upload_offsite(payload):
    from pathlib import Path

    from arcms.audit.models import Action
    from arcms.audit.services import record

    from . import offsite
    from .models import BackupRecord

    rec = BackupRecord.objects.filter(pk=payload["record"]).first()
    if rec is None or rec.status != BackupRecord.Status.OK or rec.offsite_status == "ok":
        return {"skipped": True}
    path = Path(settings.ARCMS_BACKUP_DIR) / rec.filename
    if not path.exists():
        rec.offsite_status, rec.offsite_error = "failed", "الملف المحلي لم يعد موجوداً."
        rec.save(update_fields=["offsite_status", "offsite_error"])
        return {"skipped": "missing"}
    key = offsite.object_key(rec.filename)
    try:
        offsite.upload(path, key)
    except offsite.OffsiteError as exc:
        rec.offsite_status, rec.offsite_error = "failed", str(exc)[:1000]
        rec.save(update_fields=["offsite_status", "offsite_error"])
        raise  # تُعاد المحاولة بتباعد متزايد
    rec.offsite_status, rec.offsite_key, rec.offsite_at, rec.offsite_error = "ok", key, timezone.now(), ""
    rec.save(update_fields=["offsite_status", "offsite_key", "offsite_at", "offsite_error"])
    record(Action.BACKUP, rec, message=f"رُفعت النسخة المشفّرة إلى التخزين البعيد ({settings.ARCMS_OFFSITE_BUCKET})")
    offsite.rotate_remote()
    return {"key": key}
