"""أدوات نصية عامة: الروابط العربية، زمن القراءة، تنظيف العناوين."""

from __future__ import annotations

import math
import re

from .normalize import normalize_digits, strip_diacritics

_SLUG_STRIP_RE = re.compile(r"[^\w\s-]", re.UNICODE)
_SLUG_SPACE_RE = re.compile(r"[\s_-]+", re.UNICODE)


def arabic_slugify(value: str, max_length: int = 80) -> str:
    """رابط مقروء بالعربية: «غزة: افتتاح مكتبة» → «غزة-افتتاح-مكتبة».

    نحتفظ بالحروف العربية كما هي (المتصفحات والمحركات تعرضها سليمة)،
    ونحذف التشكيل والتطويل وعلامات الترقيم فقط.
    """
    value = normalize_digits(strip_diacritics(value or ""))
    value = value.replace("،", " ").replace("؛", " ").replace("؟", " ")
    value = _SLUG_STRIP_RE.sub(" ", value)
    value = _SLUG_SPACE_RE.sub("-", value).strip("-").lower()
    if len(value) > max_length:
        value = value[:max_length].rsplit("-", 1)[0]
    return value


def reading_minutes(text: str, wpm: int = 180) -> int:
    """زمن القراءة التقريبي؛ متوسط القارئ العربي أبطأ قليلاً من الإنجليزي."""
    words = len(re.findall(r"[^\W_]+", text or ""))
    return max(1, math.ceil(words / wpm))


def clean_headline(value: str) -> str:
    """يوحّد المسافات ويحذف المحارف الخفية الملصقة من الرسائل ومواقع التواصل."""
    value = re.sub(r"[​-‏‪-‮⁦-⁩﻿]", "", value or "")
    return re.sub(r"\s+", " ", value).strip()
