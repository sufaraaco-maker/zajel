"""الأرقام والعدد والمعدود في العربية."""

from __future__ import annotations

_TO_ARABIC = str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩")
_TO_LATIN = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def to_digits(text: str, digits: str = "latin") -> str:
    """يرسم الأرقام بالشكل المطلوب: latin (0-9) أو arabic (٠-٩)."""
    text = str(text)
    if digits == "arabic":
        return text.translate(_TO_ARABIC)
    return text.translate(_TO_LATIN)


def count_phrase(n: int, singular: str, dual: str, plural: str, tamyeez: str | None = None) -> str:
    """صياغة العدد مع المعدود وفق قواعد العربية المستعملة في الصحافة.

    1 → «دقيقة»، 2 → «دقيقتين»، 3-10 → «5 دقائق»، 11-99 → «11 دقيقة»،
    والمئات تتبع آخر رقمين: 103 → «103 دقائق»، 100 → «100 دقيقة».
    """
    tamyeez = tamyeez or singular
    n = int(n)
    if n == 1:
        return singular
    if n == 2:
        return dual
    rem = n % 100
    if 3 <= rem <= 10:
        return f"{n} {plural}"
    if n > 100 and rem in (0, 1, 2):
        return f"{n} {singular}"
    if n == 0:
        return f"0 {singular}"
    return f"{n} {tamyeez}"


def compact_number(n: int, digits: str = "latin") -> str:
    """اختصار الأرقام الكبيرة في لوحة القيادة: 1.2 ألف، 3.4 مليون."""
    n = int(n or 0)
    if n >= 1_000_000:
        text = f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + " مليون"
    elif n >= 10_000:
        text = f"{n / 1000:.1f}".rstrip("0").rstrip(".") + " ألف"
    else:
        text = f"{n:,}".replace(",", "٬" if digits == "arabic" else ",")
    return to_digits(text, digits)
