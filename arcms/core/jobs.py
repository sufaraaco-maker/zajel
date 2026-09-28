"""طابور مهام خلفي فوق قاعدة البيانات، وعامل يشغّله (‎manage.py arcms_worker‎).

يكفي تشغيل عملية واحدة أو أكثر من العامل بجانب خادم الويب. المهام
تُحجز بـ SELECT … FOR UPDATE SKIP LOCKED في PostgreSQL، فلا تتكرر
مهمة بين عاملين، وتُعاد المحاولة تلقائياً مع تباعد متزايد عند الفشل.
"""

from __future__ import annotations

import logging
import os
import socket
import time
import traceback
from datetime import timedelta
from typing import Callable

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.utils import timezone

from .models import Job, WorkerHeartbeat

log = logging.getLogger("arcms.jobs")

_handlers: dict[str, Callable[[dict], dict | None]] = {}
_periodic: list[tuple[str, int, Callable[[], None]]] = []


def handler(kind: str):
    def deco(fn):
        _handlers[kind] = fn
        return fn

    return deco


def periodic(name: str, every_seconds: int):
    """مهمة دورية تُنفَّذ داخل حلقة العامل (النشر المجدول، النشرة، التجميع…)."""

    def deco(fn):
        _periodic.append((name, every_seconds, fn))
        return fn

    return deco


def enqueue(kind: str, payload: dict | None = None, *, run_after=None, dedupe_key: str | None = None, max_attempts: int = 5):
    """يضيف مهمة. dedupe_key يمنع تكرار المهمة نفسها (مثل نشرة اليوم)."""
    try:
        with transaction.atomic():
            return Job.objects.create(
                kind=kind,
                payload=payload or {},
                run_after=run_after or timezone.now(),
                dedupe_key=dedupe_key,
                max_attempts=max_attempts,
            )
    except IntegrityError:
        return Job.objects.filter(dedupe_key=dedupe_key).first()


def _claim(worker: str) -> Job | None:
    now = timezone.now()
    with transaction.atomic():
        qs = Job.objects.filter(status=Job.Status.QUEUED, run_after__lte=now).order_by("run_after", "id")
        if connection.vendor == "postgresql":
            job = qs.select_for_update(skip_locked=True).first()
        else:
            job = qs.first()
        if job is None:
            return None
        updated = Job.objects.filter(pk=job.pk, status=Job.Status.QUEUED).update(
            status=Job.Status.RUNNING, locked_by=worker, locked_at=now, attempts=job.attempts + 1
        )
        if not updated:
            return None
    job.refresh_from_db()
    return job


def run_job(job: Job) -> None:
    fn = _handlers.get(job.kind)
    try:
        if fn is None:
            raise LookupError(f"لا معالج لنوع المهمة {job.kind}")
        result = fn(job.payload) or {}
        job.status = Job.Status.DONE
        job.result = result
        job.finished_at = timezone.now()
        job.last_error = ""
    except Exception as exc:  # noqa: BLE001
        log.warning("job %s failed: %s", job, exc)
        job.last_error = f"{exc}\n{traceback.format_exc(limit=5)}"[-4000:]
        if job.attempts >= job.max_attempts or isinstance(exc, PermanentError):
            job.status = Job.Status.FAILED
            job.finished_at = timezone.now()
        else:
            job.status = Job.Status.QUEUED
            job.run_after = timezone.now() + job.backoff()
    job.locked_by = ""
    job.save()


class PermanentError(Exception):
    """خطأ لا تفيد معه إعادة المحاولة (رمز خاطئ، رقم غير صالح)."""


def run_pending(worker: str = "inline", limit: int = 100) -> int:
    load_task_modules()
    done = 0
    while done < limit:
        job = _claim(worker)
        if job is None:
            break
        run_job(job)
        done += 1
    return done


def recover_stale(minutes: int = 15) -> int:
    """مهام علقت لأن عاملاً توقف فجأة: تعود إلى الطابور."""
    cutoff = timezone.now() - timedelta(minutes=minutes)
    return Job.objects.filter(status=Job.Status.RUNNING, locked_at__lt=cutoff).update(
        status=Job.Status.QUEUED, locked_by=""
    )


def run_periodic(state: dict[str, float]) -> None:
    now = time.monotonic()
    for name, every, fn in _periodic:
        if now - state.get(name, 0) >= every:
            state[name] = now
            try:
                fn()
            except Exception:  # noqa: BLE001
                log.exception("periodic task %s failed", name)


def load_task_modules() -> None:
    from importlib import import_module

    for mod in (
        "arcms.core.tasks",
        "arcms.distribution.tasks",
        "arcms.analytics.tasks",
        "arcms.backups.tasks",
        "arcms.importer.tasks",
    ):
        import_module(mod)


def worker_loop(interval: int = 20, once: bool = False, stdout=None) -> None:
    load_task_modules()
    name = f"{socket.gethostname()}:{os.getpid()}"
    state: dict[str, float] = {}
    while True:
        if not connection.in_atomic_block:  # داخل اختبار ضمن معاملة لا نغلق الاتصال
            close_old_connections()
        busy = False
        try:
            WorkerHeartbeat.objects.update_or_create(name=name, defaults={"beat_at": timezone.now()})
            run_periodic(state)
            processed = run_pending(name)
            if stdout and processed:
                stdout.write(f"نُفّذت {processed} مهمة")
            busy = processed > 0 or Job.objects.filter(status=Job.Status.QUEUED, run_after__lte=timezone.now()).exists()
        except Exception:  # noqa: BLE001 - العامل لا يموت بسبب خطأ عابر في القاعدة
            log.exception("worker iteration failed")
        if once:
            return
        time.sleep(0.5 if busy else interval)
