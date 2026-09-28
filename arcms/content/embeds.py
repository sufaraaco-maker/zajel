"""تضمين الفيديو والمنشورات. نستخدم نطاقات «بلا تتبع» حيث أمكن (youtube-nocookie)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def youtube_id(url: str) -> str | None:
    p = urlparse(url or "")
    host = (p.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    vid = None
    if host == "youtu.be":
        vid = p.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "youtube-nocookie.com"}:
        if p.path == "/watch":
            vid = (parse_qs(p.query).get("v") or [None])[0]
        elif p.path.startswith(("/embed/", "/shorts/", "/live/")):
            vid = p.path.split("/")[2]
    return vid if vid and _YT_ID.match(vid) else None


def video_embed_url(url: str) -> str:
    if not url:
        return ""
    vid = youtube_id(url)
    if vid:
        return f"https://www.youtube-nocookie.com/embed/{vid}?rel=0"
    p = urlparse(url)
    host = (p.hostname or "").lower().removeprefix("www.")
    if host == "vimeo.com":
        m = re.match(r"^/(\d+)", p.path)
        if m:
            return f"https://player.vimeo.com/video/{m.group(1)}?dnt=1"
    if host in {"facebook.com", "fb.watch"}:
        from urllib.parse import quote

        return f"https://www.facebook.com/plugins/video.php?href={quote(url, safe='')}&show_text=false"
    return ""


EMBED_FRAME_HOSTS = (
    "https://www.youtube-nocookie.com",
    "https://player.vimeo.com",
    "https://www.facebook.com",
)
