"""جلب خلاصات الوكالات وقراءتها بأمان.

- عناوين عامة فقط (لا الشبكة الداخلية ولا الخادم نفسه) وتُفحص كل إعادة توجيه؛ الاستثناء بإعداد صريح.
- حد أقصى لحجم الخلاصة، ومهلة اتصال، وطلب مشروط (ETag / Last-Modified) لتوفير الجلب.
- XML دون تعريفات كيانات (تُرفض)، فلا توسّع أُسّي ولا كيانات خارجية.
- كل نص يُنقّى: العناوين والملخصات نص عادي، والمتن HTML منقّى بقائمة المنصة المسموحة.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import requests
from django.conf import settings
from django.utils import timezone

MAX_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 200
MAX_REDIRECTS = 3
USER_AGENT = "arcms-wires/1.0 (+newsroom feed reader)"

NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "media": "http://search.yahoo.com/mrss/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rss1": "http://purl.org/rss/1.0/",
}


class FeedError(Exception):
    pass


@dataclass
class FetchResult:
    content: bytes = b""
    not_modified: bool = False
    etag: str = ""
    modified: str = ""


@dataclass
class Entry:
    guid: str
    title: str
    link: str = ""
    summary: str = ""
    body: str = ""
    image_url: str = ""
    published_at: dt.datetime | None = None
    extra: dict = field(default_factory=dict)

    @property
    def guid_hash(self) -> str:
        return hashlib.sha256(self.guid.encode("utf-8")).hexdigest()


# --- العناوين المسموحة ---


def _resolve(host: str, port: int) -> list[str]:
    return [info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)]


def check_url(url: str) -> None:
    """يرفع FeedError إن لم يكن الرابط http(s) إلى عنوان عام."""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise FeedError("رابط غير صالح.") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FeedError("الرابط يجب أن يبدأ بـ http أو https.")
    if parts.username or parts.password:
        raise FeedError("لا تضع اسم مستخدم أو كلمة مرور داخل الرابط.")
    if settings.ARCMS_WIRE_ALLOW_PRIVATE:
        return
    try:
        addresses = _resolve(parts.hostname, port)
    except (socket.gaierror, UnicodeError) as exc:
        raise FeedError(f"تعذّر العثور على الخادم {parts.hostname}.") from exc
    for raw in addresses:
        addr = ipaddress.ip_address(raw.split("%", 1)[0])
        if not addr.is_global:
            raise FeedError("الرابط يشير إلى عنوان داخلي؛ الخلاصات تُجلب من عناوين عامة فقط.")


# --- الجلب ---


def fetch(url: str, *, etag: str = "", modified: str = "", session=None) -> FetchResult:
    session = session or requests.Session()
    headers = {"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url)
        try:
            resp = session.get(url, headers=headers, timeout=settings.ARCMS_HTTP_TIMEOUT, stream=True,
                               allow_redirects=False)
        except requests.RequestException as exc:
            raise FeedError(f"تعذّر الاتصال: {type(exc).__name__}") from exc
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("Location"):
            url = urljoin(url, resp.headers["Location"])
            resp.close()
            continue
        break
    else:
        raise FeedError("إعادات توجيه كثيرة.")
    if resp.status_code == 304:
        return FetchResult(not_modified=True, etag=etag, modified=modified)
    if resp.status_code != 200:
        raise FeedError(f"ردّ الخادم برمز {resp.status_code}.")
    chunks, size = [], 0
    for chunk in resp.iter_content(65536):
        size += len(chunk)
        if size > MAX_BYTES:
            resp.close()
            raise FeedError("الخلاصة أكبر من الحد المسموح (5 ميغابايت).")
        chunks.append(chunk)
    return FetchResult(content=b"".join(chunks), etag=resp.headers.get("ETag", "")[:200],
                       modified=resp.headers.get("Last-Modified", "")[:100])


# --- القراءة ---


def _forbid(*_args):
    raise FeedError("الخلاصة تحتوي تعريفات كيانات (DTD) وهي مرفوضة لأسباب أمنية.")


def _parse_xml(content: bytes) -> ElementTree.Element:
    # مرور أول بمحلل expat يرفض أي تعريف كيان، ثم بناء الشجرة. محلل ElementTree في CPython
    # لا يتيح هذه المعالجات مباشرة.
    from xml.parsers import expat

    check = expat.ParserCreate()
    check.EntityDeclHandler = _forbid
    check.UnparsedEntityDeclHandler = _forbid
    check.ExternalEntityRefHandler = _forbid
    try:
        check.Parse(content, True)
        return ElementTree.fromstring(content)
    except (expat.ExpatError, ElementTree.ParseError) as exc:
        raise FeedError(f"الخلاصة ليست XML صالحاً: {exc}") from exc


def _text(el, path: str) -> str:
    found = el.find(path, NS) if el is not None else None
    if found is None:
        return ""
    return "".join(found.itertext()).strip()


def plain(value: str, limit: int) -> str:
    value = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    return " ".join(value.split())[:limit]


def _date(value: str) -> dt.datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    now = timezone.now()
    return min(when, now)  # ساعة الوكالة المتقدمة لا تضع الخبر في المستقبل


def _http(url: str) -> str:
    url = (url or "").strip()
    return url[:1000] if url.startswith(("http://", "https://")) else ""


def parse(content: bytes) -> list[Entry]:
    root = _parse_xml(content)
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "feed":
        entries = [_atom_entry(e) for e in root.findall("atom:entry", NS)]
    elif tag == "rss":
        entries = [_rss_item(i) for i in root.findall("channel/item")]
    elif tag == "rdf":
        entries = [_rss_item(i, ns="rss1:") for i in root.findall("rss1:item", NS)]
    else:
        raise FeedError("صيغة غير معروفة: ليست RSS ولا Atom.")
    return [e for e in entries if e and e.title][:MAX_ENTRIES]


def _body(raw: str) -> str:
    from arcms.content.sanitize import sanitize_html

    return sanitize_html(raw[:200_000]) if raw.strip() else ""


def _rss_item(item, ns: str = "") -> Entry | None:
    title = plain(_text(item, f"{ns}title"), 500)
    link = _http(_text(item, f"{ns}link"))
    description = _text(item, f"{ns}description")
    encoded = _text(item, "content:encoded")
    guid = _text(item, "guid") or item.get(f"{{{NS['rdf']}}}about", "") or link
    date = _date(_text(item, "pubDate") or _text(item, "dc:date"))
    image = ""
    for el in (item.find("media:content", NS), item.find("media:thumbnail", NS), item.find("enclosure")):
        if el is not None and el.get("url") and (el.tag.endswith("thumbnail") or (el.get("type") or "image").startswith("image")):
            image = _http(el.get("url"))
            if image:
                break
    if not guid:
        guid = f"{title}|{date.isoformat() if date else ''}"
    return Entry(guid=guid[:2000], title=title, link=link, summary=plain(description or encoded, 2000),
                 body=_body(encoded or description), image_url=image, published_at=date)


def _atom_entry(entry) -> Entry | None:
    title = plain(_text(entry, "atom:title"), 500)
    link = ""
    image = ""
    for el in entry.findall("atom:link", NS):
        rel = el.get("rel", "alternate")
        if rel == "alternate" and not link:
            link = _http(el.get("href", ""))
        elif rel == "enclosure" and (el.get("type") or "").startswith("image") and not image:
            image = _http(el.get("href", ""))
    summary = _text(entry, "atom:summary")
    content = _text(entry, "atom:content")
    date = _date(_text(entry, "atom:published") or _text(entry, "atom:updated"))
    guid = _text(entry, "atom:id") or link or f"{title}|{date.isoformat() if date else ''}"
    return Entry(guid=guid[:2000], title=title, link=link, summary=plain(summary or content, 2000),
                 body=_body(content or summary), image_url=image, published_at=date)
