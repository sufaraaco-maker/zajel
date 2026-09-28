from urllib.parse import unquote

from django.db.models import F
from django.http import HttpResponsePermanentRedirect


def normalize_legacy_path(path: str, query: str = "") -> str:
    path = unquote(path or "/").strip()
    if not path.startswith("/"):
        path = "/" + path
    if len(path) > 1:
        path = path.rstrip("/")
    if query:
        return f"{path}?{unquote(query)}"
    return path


class LegacyRedirectMiddleware:
    """عند 404 فقط: نبحث عن الرابط في جدول الروابط القديمة."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if response.status_code != 404 or request.method not in ("GET", "HEAD"):
            return response
        from .models import LegacyRedirect

        query = request.META.get("QUERY_STRING", "")
        candidates = [normalize_legacy_path(request.path, query)] if query else []
        candidates.append(normalize_legacy_path(request.path))
        # روابط ووردبريس بصيغة ‎?p=123‎
        if query.startswith("p=") and query[2:].isdigit():
            candidates.append(f"/?p={query[2:]}")
        match = LegacyRedirect.objects.filter(old_path__in=candidates).first()
        if match:
            LegacyRedirect.objects.filter(pk=match.pk).update(hits=F("hits") + 1)
            return HttpResponsePermanentRedirect(match.new_path)
        return response
