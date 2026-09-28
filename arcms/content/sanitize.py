"""تنقية HTML المتن قبل الحفظ: قائمة بيضاء صارمة للوسوم والسمات.

أي نص يُلصق من مواقع أخرى أو من المحرر يمرّ من هنا، فلا تصل
شيفرات أو متتبعات أو أنماط غريبة إلى صفحات القرّاء.
"""

from __future__ import annotations

import re

import nh3

from .embeds import video_embed_url

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li",
    "h2", "h3", "h4", "blockquote", "figure", "figcaption", "img", "hr",
    "table", "thead", "tbody", "tr", "th", "td", "sup", "sub", "span", "div", "iframe",
}
ALLOWED_ATTRS = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "width", "height", "loading", "data-media-id"},
    "iframe": {"src", "class", "allowfullscreen", "frameborder"},
    "div": {"class", "data-embed"},
    "span": {"class"},
    "p": {"class", "dir"},
    "blockquote": {"class", "cite"},
    "figure": {"class"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
_ALLOWED_CLASSES = {
    "ql-align-center", "ql-align-left", "ql-align-right", "ql-align-justify", "ql-direction-rtl",
    "embed", "embed-video", "pullquote", "note", "highlight", "ql-video",
}


def _filter_attr(tag: str, attr: str, value: str) -> str | None:
    if attr == "class":
        kept = [c for c in value.split() if c in _ALLOWED_CLASSES]
        return " ".join(kept) or None
    if attr == "target":
        return "_blank" if value == "_blank" else None
    if attr == "data-embed":
        return value if video_embed_url(value) else None
    if tag == "iframe" and attr == "src":
        # الإطارات المسموحة فقط: يوتيوب (بلا تتبع) وفيميو وفيسبوك، بعد إعادة بناء الرابط.
        return video_embed_url(value) or None
    return value


def sanitize_html(html: str) -> str:
    if not html:
        return ""
    # محررات النصوص تضع فقرات فارغة كثيرة عند اللصق.
    html = re.sub(r"(<p><br\s*/?></p>\s*){2,}", "<p><br></p>", html)
    html = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        attribute_filter=_filter_attr,
        url_schemes={"http", "https", "mailto", "tel"},
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    # إطار رُفض رابطه يبقى فارغاً؛ نحذفه.
    return re.sub(r"<iframe(?![^>]*\ssrc=)[^>]*>\s*</iframe>", "", html).strip()


def render_embeds(html: str) -> str:
    """يحوّل عناصر ‎<div data-embed="url">‎ إلى إطارات تضمين آمنة عند العرض."""

    def repl(m: re.Match) -> str:
        src = video_embed_url(m.group(1))
        if not src:
            return ""
        return (
            f'<div class="embed-video"><iframe src="{src}" loading="lazy" '
            'allow="encrypted-media; picture-in-picture; fullscreen" '
            'referrerpolicy="strict-origin-when-cross-origin" title="فيديو مضمَّن"></iframe></div>'
        )

    return re.sub(r'<div[^>]*data-embed="([^"]+)"[^>]*>.*?</div>', repl, html or "", flags=re.S)


def plain_text(html: str) -> str:
    # مسافة مكان نهاية كل كتلة، حتى لا تلتصق الجمل عند نزع الوسوم.
    html = re.sub(r"</(p|h\d|li|div|blockquote|figcaption|td|tr)>|<br\s*/?>", " ", html or "")
    text = nh3.clean(html, tags=set())
    import html as _html

    return re.sub(r"\s+", " ", _html.unescape(text)).strip()
