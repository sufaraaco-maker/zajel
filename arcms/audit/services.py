"""تسجيل القيود وتتبع الفاعل (المستخدم وعنوانه) عبر سياق الطلب."""

from __future__ import annotations

import contextvars
import datetime as dt
import decimal
import uuid
from dataclasses import dataclass

from django.db import connection, models, transaction
from django.db.models.fields.files import FieldFile
from django.db.models.signals import post_delete, post_save, pre_save

from .models import Action, AuditEntry

GENESIS = "0" * 64
_LOCK_KEY = 0x61726373  # «arcs»


@dataclass
class Actor:
    user: object | None = None
    ip: str | None = None
    user_agent: str = ""
    label: str = ""


_current: contextvars.ContextVar[Actor | None] = contextvars.ContextVar("arcms_audit_actor", default=None)
_suppressed: contextvars.ContextVar[bool] = contextvars.ContextVar("arcms_audit_suppressed", default=False)


class suppressed:
    """إيقاف التسجيل التلقائي لكل سجل أثناء العمليات الجماعية (الاستيراد)،
    على أن تُسجَّل العملية كلها بقيد واحد موجز."""

    def __enter__(self):
        self._token = _suppressed.set(True)

    def __exit__(self, *exc):
        _suppressed.reset(self._token)


def set_actor(actor: Actor | None):
    return _current.set(actor)


def reset_actor(token) -> None:
    _current.reset(token)


def current_actor() -> Actor:
    return _current.get() or Actor(label="النظام")


class acting_as:
    """سياق لتنفيذ عمليات باسم مستخدم أو باسم «النظام» (العامل الخلفي، الأوامر)."""

    def __init__(self, user=None, label: str = ""):
        self.actor = Actor(user=user, label=label or (str(user) if user else "النظام"))

    def __enter__(self):
        self._token = set_actor(self.actor)
        return self.actor

    def __exit__(self, *exc):
        reset_actor(self._token)


def record(
    action: str,
    obj: models.Model | None = None,
    *,
    message: str = "",
    changes: dict | None = None,
    object_type: str = "",
    object_id: str = "",
    object_repr: str = "",
    actor: Actor | None = None,
) -> AuditEntry:
    actor = actor or current_actor()
    user = actor.user if getattr(actor.user, "pk", None) else None
    if obj is not None:
        object_type = object_type or obj._meta.label_lower
        object_id = object_id or str(obj.pk or "")
        object_repr = object_repr or str(obj)[:255]
    with transaction.atomic():
        if connection.vendor == "postgresql":
            with connection.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", [_LOCK_KEY])
        last = AuditEntry.objects.order_by("-id").only("hash").first()
        entry = AuditEntry(
            actor=user,
            actor_label=(actor.label or (str(user) if user else "النظام"))[:150],
            ip=actor.ip,
            user_agent=(actor.user_agent or "")[:200],
            action=action,
            object_type=object_type[:60],
            object_id=str(object_id)[:64],
            object_repr=object_repr[:255],
            message=message[:500],
            changes=_jsonable(changes or {}),
            prev_hash=last.hash if last else GENESIS,
        )
        entry.hash = entry.compute_hash()
        entry.save()
    return entry


def verify_chain(limit: int | None = None) -> tuple[bool, int, AuditEntry | None]:
    """يفحص السلسلة كاملة. يعيد (سليمة؟، عدد القيود المفحوصة، أول قيد مكسور)."""
    prev = GENESIS
    count = 0
    qs = AuditEntry.objects.order_by("id")
    for entry in qs.iterator(chunk_size=2000):
        if entry.prev_hash != prev or entry.compute_hash() != entry.hash:
            return False, count, entry
        prev = entry.hash
        count += 1
        if limit and count >= limit:
            break
    return True, count, None


# --- مراسي السلسلة خارج الخادم ---
#
# من يملك الكتابة في القاعدة يستطيع إعادة حساب السلسلة كلها أو حذف ذيلها. «المرساة»
# رقم آخر قيد وتجزئته، تُرسل يومياً بالبريد إلى المدققين فتحفظ خارج الخادم؛ وأي إعادة
# كتابة لما قبلها تغيّر تجزئتها فتُكشف بمقارنتها.


def current_anchor() -> str:
    last = AuditEntry.objects.order_by("-id").only("id", "hash").first()
    return f"{last.pk}:{last.hash}" if last else ""


def check_anchor(anchor: str) -> tuple[bool, str]:
    """يتحقق من أن القيد المثبَّت ما زال في السجل بالتجزئة نفسها وأن السلسلة سليمة."""
    raw = (anchor or "").strip().lstrip("#")
    entry_id, _, digest = raw.partition(":")
    if not entry_id.isdigit() or len(digest) != 64:
        return False, "صيغة المرساة غير صحيحة؛ المتوقع: الرقم:التجزئة"
    entry = AuditEntry.objects.filter(pk=int(entry_id)).first()
    if entry is None:
        return False, f"القيد #{entry_id} غير موجود: حُذف جزء من السجل."
    if entry.hash != digest.lower():
        return False, f"تجزئة القيد #{entry_id} تغيّرت: أُعيدت كتابة السجل حتى هذا القيد."
    ok, _count, broken = verify_chain()
    if not ok:
        return False, f"السلسلة مكسورة عند القيد #{broken.pk}."
    later = AuditEntry.objects.filter(pk__gt=entry.pk).count()
    return True, f"المرساة مطابقة، والسلسلة سليمة، وبعدها {later} قيداً."


# --- تسجيل التغييرات على النماذج تلقائياً ---

_registry: dict[type, dict] = {}


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, models.Model):
        return value.pk
    if isinstance(value, FieldFile):  # ملفات (والحقل الفارغ لا رابط له)
        return value.name or ""
    return value


def _snapshot(instance: models.Model, exclude: set[str]) -> dict:
    data = {}
    for field in instance._meta.concrete_fields:
        if field.name in exclude:
            continue
        value = getattr(instance, field.attname)
        if isinstance(value, str) and len(value) > 400:
            value = value[:400] + "…"
        data[field.name] = _jsonable(value)
    return data


_ALWAYS_EXCLUDE = {"updated_at", "last_login", "last_seen_at", "totp_last_counter", "view_count"}
_SECRET_FIELDS = {"password", "totp_secret", "recovery_codes", "source_notes"}


def register(model: type[models.Model], exclude: tuple[str, ...] = (), label: str = "") -> None:
    """يضيف نموذجاً إلى التسجيل التلقائي للإنشاء والتعديل والحذف."""
    opts = {"exclude": set(exclude) | _ALWAYS_EXCLUDE, "label": label}
    _registry[model] = opts
    uid = f"audit-{model._meta.label_lower}"
    pre_save.connect(_pre_save, sender=model, dispatch_uid=uid + "-pre", weak=False)
    post_save.connect(_post_save, sender=model, dispatch_uid=uid + "-post", weak=False)
    post_delete.connect(_post_delete, sender=model, dispatch_uid=uid + "-del", weak=False)


def _pre_save(sender, instance, raw=False, **kwargs):
    if raw or not instance.pk or _suppressed.get():
        instance._audit_before = None
        return
    try:
        old = sender._base_manager.get(pk=instance.pk)
    except sender.DoesNotExist:
        instance._audit_before = None
        return
    instance._audit_before = _snapshot(old, _registry[sender]["exclude"])


def _mask(changes: dict) -> dict:
    for key in list(changes):
        if key in _SECRET_FIELDS:
            changes[key] = ["•••", "•••"] if isinstance(changes[key], list) else "•••"
    return changes


def _post_save(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw or _suppressed.get():
        return
    exclude = _registry[sender]["exclude"]
    if update_fields and set(update_fields) <= exclude:
        return
    after = _snapshot(instance, exclude)
    if created:
        record(Action.CREATE, instance, changes=_mask({k: v for k, v in after.items() if v not in (None, "", [], {})}))
        return
    before = getattr(instance, "_audit_before", None) or {}
    diff = {k: [before.get(k), v] for k, v in after.items() if before.get(k) != v}
    if diff:
        record(Action.UPDATE, instance, changes=_mask(diff))


def _post_delete(sender, instance, **kwargs):
    if _suppressed.get():
        return
    record(Action.DELETE, instance, changes=_mask(_snapshot(instance, _registry[sender]["exclude"])))
