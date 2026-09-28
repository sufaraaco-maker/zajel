"""التواريخ بصياغة الصحافة العربية.

تختلف أسماء الأشهر بين الأقاليم: «أيلول» في بلاد الشام والعراق،
«سبتمبر» في مصر والخليج، «شتنبر» في المغرب، «سبتمبر» مع «جانفي/فيفري»
في الجزائر وتونس. وكثير من الصحف الشامية تكتب الاسمين معاً «أيلول/سبتمبر».
كذلك يختلف رسم الأرقام (0-9 أو ٠-٩)، ويُلحق التاريخ الهجري في بعض المؤسسات.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone

from .numbers import count_phrase, to_digits

WEEKDAYS = ("الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد")

MONTHS = {
    "levant": (
        "كانون الثاني", "شباط", "آذار", "نيسان", "أيار", "حزيران",
        "تموز", "آب", "أيلول", "تشرين الأول", "تشرين الثاني", "كانون الأول",
    ),
    "egypt": (
        "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
        "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    ),
    "morocco": (
        "يناير", "فبراير", "مارس", "أبريل", "ماي", "يونيو",
        "يوليوز", "غشت", "شتنبر", "أكتوبر", "نونبر", "دجنبر",
    ),
    "algeria": (
        "جانفي", "فيفري", "مارس", "أفريل", "ماي", "جوان",
        "جويلية", "أوت", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
    ),
}

MONTH_STYLES = (
    ("dual", "مزدوج: أيلول/سبتمبر"),
    ("levant", "شامي: أيلول"),
    ("egypt", "مصري وخليجي: سبتمبر"),
    ("morocco", "مغربي: شتنبر"),
    ("algeria", "جزائري وتونسي: سبتمبر/جانفي"),
)

HIJRI_MONTHS = (
    "محرم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
    "رجب", "شعبان", "رمضان", "شوال", "ذو القعدة", "ذو الحجة",
)


@dataclass(frozen=True)
class DateStyle:
    """إعدادات صياغة التاريخ لمؤسسة بعينها (تأتي من إعدادات الموقع)."""

    months: str = "dual"
    digits: str = "latin"  # latin | arabic
    clock: str = "12"  # 12 | 24
    hijri: bool = False
    hijri_adjust: int = 0  # تعديل يوم أو يومين حسب الرؤية المحلية


DEFAULT_STYLE = DateStyle()


def month_name(month: int, style: DateStyle = DEFAULT_STYLE) -> str:
    if style.months == "dual":
        levant, egypt = MONTHS["levant"][month - 1], MONTHS["egypt"][month - 1]
        return f"{levant}/{egypt}"
    return MONTHS.get(style.months, MONTHS["egypt"])[month - 1]


def _local(value: datetime | date) -> datetime | date:
    if isinstance(value, datetime) and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def format_date(value: datetime | date, style: DateStyle = DEFAULT_STYLE, weekday: bool = True) -> str:
    """«الأحد 28 أيلول/سبتمبر 2026»."""
    if value is None:
        return ""
    value = _local(value)
    text = f"{value.day} {month_name(value.month, style)} {value.year}"
    if weekday:
        text = f"{WEEKDAYS[value.weekday()]} {text}"
    return to_digits(text, style.digits)


def format_time(value: datetime, style: DateStyle = DEFAULT_STYLE) -> str:
    """«10:30 مساءً» أو «22:30»."""
    if value is None:
        return ""
    value = _local(value)
    if style.clock == "24":
        text = f"{value.hour:02d}:{value.minute:02d}"
    else:
        hour = value.hour % 12 or 12
        period = "صباحًا" if value.hour < 12 else "مساءً"
        text = f"{hour}:{value.minute:02d} {period}"
    return to_digits(text, style.digits)


def format_datetime(value: datetime, style: DateStyle = DEFAULT_STYLE) -> str:
    """«الأحد 28 أيلول/سبتمبر 2026 | 10:30 مساءً»."""
    if value is None:
        return ""
    return f"{format_date(value, style)} | {format_time(value, style)}"


def to_hijri(value: date, adjust: int = 0) -> tuple[int, int, int] | None:
    """التحويل إلى التقويم الهجري (أم القرى) مع تعديل اختياري بالأيام."""
    try:
        from hijridate import Gregorian
    except ImportError:  # pragma: no cover - الاعتمادية ضمن المتطلبات
        return None
    if isinstance(value, datetime):
        value = _local(value).date()
    value = value + timedelta(days=adjust)
    try:
        h = Gregorian(value.year, value.month, value.day).to_hijri()
    except (OverflowError, ValueError):
        return None
    return h.year, h.month, h.day


def format_hijri(value: date, style: DateStyle = DEFAULT_STYLE) -> str:
    """«17 ربيع الآخر 1448 هـ»."""
    h = to_hijri(value, style.hijri_adjust)
    if not h:
        return ""
    year, month, day = h
    return to_digits(f"{day} {HIJRI_MONTHS[month - 1]} {year} هـ", style.digits)


def masthead_date(value: datetime, style: DateStyle = DEFAULT_STYLE) -> str:
    """سطر التاريخ في رأس الصفحة، مع الهجري إن كان مفعّلاً."""
    text = format_date(value, style)
    if style.hijri:
        hijri = format_hijri(value, style)
        if hijri:
            text = f"{text} - {hijri}"
    return text


# وحدات الزمن: (المفرد، المثنى المجرور/المنصوب، الجمع، التمييز المنصوب لـ 11-99)
_UNITS = {
    "second": ("ثانية", "ثانيتين", "ثوانٍ", "ثانية"),
    "minute": ("دقيقة", "دقيقتين", "دقائق", "دقيقة"),
    "hour": ("ساعة", "ساعتين", "ساعات", "ساعة"),
    "day": ("يوم", "يومين", "أيام", "يومًا"),
    "week": ("أسبوع", "أسبوعين", "أسابيع", "أسبوعًا"),
    "month": ("شهر", "شهرين", "أشهر", "شهرًا"),
    "year": ("سنة", "سنتين", "سنوات", "سنة"),
}


def duration_phrase(n: int, unit: str, digits: str = "latin") -> str:
    """«دقيقة»، «دقيقتين»، «5 دقائق»، «11 دقيقة»، «100 يوم»."""
    singular, dual, plural, tamyeez = _UNITS[unit]
    return to_digits(count_phrase(n, singular, dual, plural, tamyeez), digits)


def relative_time(
    value: datetime,
    now: datetime | None = None,
    style: DateStyle = DEFAULT_STYLE,
    max_days: int = 7,
) -> str:
    """«منذ 5 دقائق»، «منذ ساعتين»، «أمس 10:30 مساءً»، ثم التاريخ الكامل."""
    if value is None:
        return ""
    now = now or timezone.now()
    delta = now - value
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return format_datetime(value, style)
    if seconds < 45:
        return "الآن"
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return "منذ " + duration_phrase(minutes, "minute", style.digits)
    if seconds < 86400:
        hours = seconds // 3600
        return "منذ " + duration_phrase(hours, "hour", style.digits)
    local_value, local_now = _local(value), _local(now)
    days = (local_now.date() - local_value.date()).days
    if days == 1:
        return "أمس " + format_time(value, style)
    if days <= max_days:
        return "منذ " + duration_phrase(days, "day", style.digits)
    return format_date(value, style, weekday=False)
