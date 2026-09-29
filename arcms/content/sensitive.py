"""الصور القاسية: تُعلَّم في المكتبة، فتظهر في الموقع مموّهة حتى يختار القارئ رؤيتها.

صور القوالب تحمل العلامة مباشرة من الوسيط (data-sensitive). أما صور المتن فمخزّنة روابطَ في
نص المادة، فتُعرف هنا من قائمة روابط الصور المعلَّمة (مخزنة مؤقتاً وتُمسح عند تعديل أي وسيط)،
فيسري تعليم الصورة أو إلغاؤه على كل مادة تستخدمها دون إعادة حفظها.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from django.core.cache import cache

CACHE_KEY = "arcms:sensitive-media-urls"
_IMG = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
_SRC = re.compile(r"""\bsrc\s*=\s*["']([^"']+)["']""", re.IGNORECASE)


def sensitive_urls() -> frozenset[str]:
    urls = cache.get(CACHE_KEY)
    if urls is None:
        from .models import MediaAsset

        found: set[str] = set()
        for asset in MediaAsset.objects.filter(sensitive=True).only("file", "renditions"):
            for url in [asset.url, *(asset.rendition(name) for name in (asset.renditions or {}))]:
                if url:
                    found.update({url, urlsplit(url).path})
        urls = frozenset(found)
        cache.set(CACHE_KEY, urls, 3600)
    return urls


def forget(*args, **kwargs) -> None:
    cache.delete(CACHE_KEY)


def mark_body(html: str) -> str:
    """يضيف data-sensitive لصور المتن المعلَّمة في المكتبة."""
    if not html or "<img" not in html.lower():
        return html
    urls = sensitive_urls()
    if not urls:
        return html

    def mark(match: re.Match) -> str:
        tag = match.group(0)
        src = _SRC.search(tag)
        if not src or "data-sensitive" in tag:
            return tag
        if src.group(1) in urls or urlsplit(src.group(1)).path in urls:
            return tag[:4] + " data-sensitive" + tag[4:]
        return tag

    return _IMG.sub(mark, html)
