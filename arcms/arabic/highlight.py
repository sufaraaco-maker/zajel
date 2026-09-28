"""تظليل كلمات البحث في المقتطفات مع احترام التطبيع والتجذيع."""

from __future__ import annotations

import re
from html import escape

from .analyzer import ParsedQuery, word_keys

_WORD_RE = re.compile(r"[^\W_]+|[\W_]+", re.UNICODE)


def _hit(word: str, keys: frozenset[str]) -> bool:
    return bool(word_keys(word) & keys)


def highlight(text: str, query: ParsedQuery, tag: str = "mark") -> str:
    """يعيد HTML آمناً مع إحاطة الكلمات المطابقة بوسم <mark>."""
    if not text:
        return ""
    if query.is_empty:
        return escape(text)
    keys = query.all_keys
    parts = []
    for piece in _WORD_RE.findall(text):
        if piece[:1].isalnum() and _hit(piece, keys):
            parts.append(f"<{tag}>{escape(piece)}</{tag}>")
        else:
            parts.append(escape(piece))
    return "".join(parts)


def snippet(text: str, query: ParsedQuery, length: int = 220) -> str:
    """مقتطف حول أول تطابق في النص، مظلَّل وآمن للعرض."""
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    start = 0
    if not query.is_empty:
        keys = query.all_keys
        for m in re.finditer(r"[^\W_]+", text):
            if _hit(m.group(0), keys):
                start = max(0, m.start() - length // 3)
                break
    if start:
        # لا نبدأ من منتصف كلمة.
        space = text.find(" ", start)
        start = space + 1 if 0 <= space < start + 20 else start
    piece = text[start : start + length]
    if start + length < len(text):
        cut = piece.rfind(" ")
        piece = (piece[:cut] if cut > length // 2 else piece) + "…"
    if start:
        piece = "…" + piece
    return highlight(piece, query)
