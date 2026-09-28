"""مرشّحات القوالب العربية: التواريخ الصحفية والأرقام والعدد والمعدود."""

from __future__ import annotations

from django import template
from django.utils.safestring import mark_safe

from arcms.arabic import dates
from arcms.arabic.numbers import compact_number, count_phrase, to_digits

register = template.Library()


def _style():
    from arcms.core.models import SiteSettings

    try:
        return SiteSettings.load().date_style
    except Exception:  # noqa: BLE001 - قبل إنشاء الجداول
        return dates.DEFAULT_STYLE


@register.filter
def ar_date(value, weekday=True):
    return dates.format_date(value, _style(), weekday=bool(weekday)) if value else ""


@register.filter
def ar_date_short(value):
    return dates.format_date(value, _style(), weekday=False) if value else ""


@register.filter
def ar_time(value):
    return dates.format_time(value, _style()) if value else ""


@register.filter
def ar_datetime(value):
    return dates.format_datetime(value, _style()) if value else ""


@register.filter
def ar_relative(value):
    return dates.relative_time(value, style=_style()) if value else ""


@register.filter
def ar_hijri(value):
    return dates.format_hijri(value, _style()) if value else ""


@register.filter
def masthead_date(value):
    return dates.masthead_date(value, _style()) if value else ""


@register.filter
def ar_digits(value):
    return to_digits(value, _style().digits)


@register.filter
def ar_compact(value):
    return compact_number(value or 0, _style().digits)


@register.simple_tag
def ar_count(n, singular, dual, plural, tamyeez=""):
    return to_digits(count_phrase(n or 0, singular, dual, plural, tamyeez or None), _style().digits)


@register.filter
def isoformat(value):
    return value.isoformat() if value else ""


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None


@register.filter
def percent_of(value, total):
    try:
        return round(100 * float(value) / float(total)) if total else 0
    except (TypeError, ValueError):
        return 0


@register.simple_tag(takes_context=True)
def can(context, cap):
    user = context.get("user") or getattr(context.get("request"), "user", None)
    return bool(user and user.is_authenticated and user.can(cap))


@register.filter
def icon(name, size=18):
    from django.templatetags.static import static

    return mark_safe(
        f'<svg class="icon" width="{int(size)}" height="{int(size)}" aria-hidden="true">'
        f'<use href="{static("img/icons.svg")}#{name}"></use></svg>'
    )


@register.filter
def body_split(html, n=3):
    """يقسم المتن بعد الفقرة رقم n لإدراج إعلان أو صندوق داخل المقال."""
    marker = "</p>"
    idx = -1
    for _ in range(int(n)):
        idx = (html or "").find(marker, idx + 1)
        if idx == -1:
            return [mark_safe(html or ""), mark_safe("")]
    cut = idx + len(marker)
    return [mark_safe(html[:cut]), mark_safe(html[cut:])]


@register.filter
def localdate(value):
    from django.utils import timezone

    return timezone.localtime(value).date() if value else None


@register.filter
def dict_items(value):
    """أزواج قاموس للحلقات. ‎d.items‎ في القوالب تُقرأ مفتاحاً إن وُجد مفتاح اسمه items."""
    return list(value.items()) if isinstance(value, dict) else []


@register.filter
def split_words(value):
    return str(value).split()


@register.filter
def getfield(form, name):
    """حقل نموذج باسمه (للقوالب التي ترتّب الحقول يدوياً)."""
    return form[name]
