from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlparse

from django.conf import settings


def client_ip(request) -> str | None:
    """عنوان العميل. خلف الوكيل العكسي نثق بترويسة X-Real-IP التي يضعها nginx/Caddy."""
    meta = request.META
    candidates = []
    if getattr(settings, "SECURE_PROXY_SSL_HEADER", None):
        candidates.append(meta.get("HTTP_X_REAL_IP", ""))
        xff = meta.get("HTTP_X_FORWARDED_FOR", "")
        if xff:
            candidates.append(xff.split(",")[-1])
    candidates.append(meta.get("REMOTE_ADDR", ""))
    for raw in candidates:
        raw = (raw or "").strip()
        if not raw:
            continue
        try:
            return str(ipaddress.ip_address(raw))
        except ValueError:
            continue
    return None


def absolute_url(path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    return settings.SITE_URL + (path if path.startswith("/") else "/" + path)


def is_safe_redirect(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return not parsed.netloc and not parsed.scheme and url.startswith("/") and not url.startswith("//")


_SCRIPT_ESCAPES = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026"}


def json_script_safe(data) -> str:
    """JSON يُضمَّن داخل <script> دون أن يغلقه نص مثل «</script>» في عنوان مادة."""
    return json.dumps(data, ensure_ascii=False).translate(_SCRIPT_ESCAPES)
