"""قراءة الأرشيف من مصادره: ملف تصدير ووردبريس (WXR) أو ملف JSON Lines.

كل مصدر يحوّل موادّه إلى «سجل موحّد» (Record) يعالجه المحمِّل بالطريقة نفسها.
القراءة تدفّقية، فيُستورد أرشيف بمئات آلاف المواد دون تحميله في الذاكرة.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timezone as dt_tz
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterator


@dataclass
class Record:
    legacy_id: str
    title: str
    body_html: str = ""
    excerpt: str = ""
    subtitle: str = ""
    published_at: datetime | None = None
    status: str = "published"
    kind: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    image_url: str = ""
    image_caption: str = ""
    legacy_url: str = ""
    extra_legacy_paths: list[str] = field(default_factory=list)
    source: str = ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _ns(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _child_text(el: ET.Element, name: str, ns_hint: str = "") -> str:
    for child in el:
        if _local(child.tag) == name and (not ns_hint or ns_hint in _ns(child.tag)):
            return (child.text or "").strip()
    return ""


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value or value.startswith("0000"):
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=dt_tz.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=dt_tz.utc)
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


_CAPTION_RE = re.compile(r"\[caption[^\]]*\](.*?)\[/caption\]", re.S)
_SHORTCODE_RE = re.compile(r"\[/?[a-zA-Z_][^\]]*\]")


def wordpress_to_html(content: str) -> str:
    """يحوّل متن ووردبريس الخام: الفقرات، ووسوم [caption]، وحذف بقية الرموز المختصرة."""

    def caption(m: re.Match) -> str:
        inner = m.group(1)
        img = re.search(r"<img[^>]*>", inner)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        return f"<figure>{img.group(0) if img else ''}<figcaption>{text}</figcaption></figure>"

    content = _CAPTION_RE.sub(caption, content or "")
    content = _SHORTCODE_RE.sub("", content)
    if "<p" not in content:
        blocks = re.split(r"\n\s*\n", content.strip())
        content = "".join(
            b if re.match(r"\s*<(figure|h\d|ul|ol|blockquote|table|div)", b) else f"<p>{b.strip().replace(chr(10), '<br>')}</p>"
            for b in blocks
            if b.strip()
        )
    return content


def read_wxr(path: Path) -> Iterator[Record]:
    """ملف تصدير ووردبريس (أدوات ← تصدير). يدعم الإصدارات 1.0 إلى 1.2 من الصيغة."""
    attachments: dict[str, str] = {}
    authors: dict[str, str] = {}
    # المرور الأول: المرفقات (لربط الصورة البارزة) وأسماء الكتّاب.
    for _, el in ET.iterparse(path, events=("end",)):
        name = _local(el.tag)
        if name == "author" and "wordpress.org/export" in _ns(el.tag):
            login = _child_text(el, "author_login")
            display = _child_text(el, "author_display_name") or login
            if login:
                authors[login] = display
            el.clear()
        elif name == "item":
            if _child_text(el, "post_type") == "attachment":
                url = _child_text(el, "attachment_url")
                if url:
                    attachments[_child_text(el, "post_id")] = url
            el.clear()

    for _, el in ET.iterparse(path, events=("end",)):
        if _local(el.tag) != "item":
            continue
        post_type = _child_text(el, "post_type")
        status = _child_text(el, "status")
        if post_type != "post" or status not in ("publish", "draft", "future", "private"):
            el.clear()
            continue
        post_id = _child_text(el, "post_id")
        categories, tags = [], []
        thumb_id = ""
        body = excerpt = ""
        for child in el:
            local = _local(child.tag)
            if local == "category":
                label = (child.text or "").strip()
                if child.get("domain") == "category" and label:
                    categories.append(label)
                elif child.get("domain") == "post_tag" and label:
                    tags.append(label)
            elif local == "postmeta":
                if _child_text(child, "meta_key") == "_thumbnail_id":
                    thumb_id = _child_text(child, "meta_value")
            elif local == "encoded":
                if "excerpt" in _ns(child.tag):
                    excerpt = (child.text or "").strip()
                else:
                    body = child.text or ""
        creator = _child_text(el, "creator")
        link = _child_text(el, "link")
        yield Record(
            legacy_id=f"wp:{post_id}",
            title=_child_text(el, "title") or "(بلا عنوان)",
            body_html=wordpress_to_html(body),
            excerpt=re.sub(r"<[^>]+>", "", excerpt),
            published_at=_parse_dt(_child_text(el, "post_date_gmt")) or _parse_dt(_child_text(el, "pubDate")),
            status="published" if status == "publish" else "draft",
            categories=categories,
            tags=tags,
            authors=[authors.get(creator, creator)] if creator else [],
            image_url=attachments.get(thumb_id, ""),
            legacy_url=link,
            extra_legacy_paths=[f"/?p={post_id}"] if post_id else [],
        )
        el.clear()


def read_jsonl(path: Path) -> Iterator[Record]:
    """سطر JSON لكل مادة. الحقول: id, title, body, excerpt, subtitle, published_at, status,
    kind, category|categories, tags, author|authors, image_url, image_caption, url, source."""
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"سطر {n}: JSON غير صالح ({exc})") from exc
            cats = d.get("categories") or ([d["category"]] if d.get("category") else [])
            auths = d.get("authors") or ([d["author"]] if d.get("author") else [])
            yield Record(
                legacy_id=f"json:{d.get('id') or n}",
                title=str(d.get("title") or "(بلا عنوان)"),
                body_html=str(d.get("body") or ""),
                excerpt=str(d.get("excerpt") or ""),
                subtitle=str(d.get("subtitle") or ""),
                published_at=_parse_dt(str(d.get("published_at") or "")),
                status="draft" if d.get("status") == "draft" else "published",
                kind=str(d.get("kind") or ""),
                categories=[str(c) for c in cats],
                tags=[str(t) for t in d.get("tags") or []],
                authors=[str(a) for a in auths],
                image_url=str(d.get("image_url") or ""),
                image_caption=str(d.get("image_caption") or ""),
                legacy_url=str(d.get("url") or ""),
                source=str(d.get("source") or ""),
            )
