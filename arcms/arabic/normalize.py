"""تطبيع النص العربي لأغراض البحث والمقارنة.

القاعدة: لا نغيّر النص المعروض للقارئ أبداً؛ التطبيع يُطبَّق فقط على
نسخة الفهرسة وعلى استعلام البحث، حتى يتطابق «أحمد» مع «احمد» و«مسؤول»
مع «مسئول» و«فِلَسطين» مع «فلسطين».
"""

from __future__ import annotations

import re
import unicodedata

# الحركات والتنوين والشدة والسكون والألف الخنجرية وعلامات المصحف.
_DIACRITICS = "ؐ-ًؚ-ٰٟۖ-ۜ۟-۪ۨ-ۭ"
_TATWEEL = "ـ"
# محارف التحكم في الاتجاه وعلامات العرض الخفية التي تُلصق كثيراً عند النسخ.
_INVISIBLE = "​-‏‪-‮⁦-⁩﻿"

_STRIP_RE = re.compile(f"[{_DIACRITICS}{_TATWEEL}{_INVISIBLE}]")

_ALEF_FORMS = "أإآٱٲٳٵ"
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# خريطة التطبيع الأساسية: صور الألف، الألف المقصورة، التاء المربوطة،
# والحروف الفارسية/الأردية التي تظهر في نصوص منسوخة من مصادر أخرى.
_BASE_MAP = str.maketrans(
    {
        **{c: "ا" for c in _ALEF_FORMS},
        "ى": "ي",
        "ة": "ه",
        "ؤ": "و",
        "ئ": "ي",
        "ک": "ك",
        "ی": "ي",
        "ې": "ي",
        "ە": "ه",
        "ۀ": "ه",
        "ﻻ": "لا",
        "ﻷ": "لا",
        "ﻹ": "لا",
        "ﻵ": "لا",
    }
)

# خريطة «الهيكل بلا همزات»: تحذف الهمزة ومقاعدها كلياً ليتطابق
# «مسؤول» و«مسئول» و«مسءول» عند اختلاف الكتّاب في رسم الهمزة.
_HAMZA_SKELETON_MAP = str.maketrans(
    {
        **{c: "ا" for c in _ALEF_FORMS},
        "ء": None,
        "ؤ": None,
        "ئ": None,
        "ى": "ي",
        "ة": "ه",
        "ک": "ك",
        "ی": "ي",
    }
)


def strip_diacritics(text: str) -> str:
    """يحذف التشكيل والتطويل والمحارف الخفية مع إبقاء الحروف كما هي."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return _STRIP_RE.sub("", text)


def normalize_digits(text: str) -> str:
    """يحوّل الأرقام الهندية (٠١٢) والفارسية (۰۱۲) إلى أرقام عربية غربية."""
    return text.translate(_DIGITS)


def normalize(text: str) -> str:
    """التطبيع القياسي للفهرسة: تشكيل، همزات على الألف، ى/ي، ة/ه، أرقام، حالة الأحرف اللاتينية."""
    if not text:
        return ""
    text = strip_diacritics(text)
    text = normalize_digits(text)
    text = text.translate(_BASE_MAP)
    return text.lower()


def hamza_skeleton(text: str) -> str:
    """صورة الكلمة بعد حذف الهمزة ومقاعدها؛ تُستخدم مفتاحاً إضافياً للتسامح مع الهمزات."""
    if not text:
        return ""
    text = strip_diacritics(text)
    text = normalize_digits(text)
    return text.translate(_HAMZA_SKELETON_MAP).lower()


_ARABIC_LETTER_RE = re.compile(r"[ؠ-يٱ-ۓ]")


def is_arabic(text: str) -> bool:
    return bool(_ARABIC_LETTER_RE.search(text or ""))
