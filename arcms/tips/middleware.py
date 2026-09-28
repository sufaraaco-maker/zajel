"""فصل صندوق المعلومات على نطاقه الخاص حين يُضبط ARCMS_TIPS_HOST.

على نطاق الصندوق: لا يُخدم إلا صفحات الصندوق وملفاته الثابتة، فلا غرفة تحرير ولا
صفحات موقع ولا وسائط. وعلى النطاق الرئيسي: روابط الصندوق تُحوَّل إلى نطاقه.
"""

from __future__ import annotations

from django.conf import settings
from django.http import HttpResponseNotFound, HttpResponseRedirect

_TIPS_PREFIX = "/tips/"


class TipsHostMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        tips_host = settings.ARCMS_TIPS_HOST
        if tips_host:
            host = request.get_host().lower()
            if not host.startswith("["):  # IPv6 حرفي لا يكون نطاق الصندوق
                host = host.rsplit(":", 1)[0]
            if host == tips_host:
                if request.path == "/":
                    return HttpResponseRedirect(_TIPS_PREFIX)
                if not request.path.startswith((_TIPS_PREFIX, "/static/")):
                    return HttpResponseNotFound("")
                request.arcms_tips_host = True
            elif request.path.startswith(_TIPS_PREFIX):
                return HttpResponseRedirect(settings.ARCMS_TIPS_ORIGIN + request.get_full_path())
        return self.get_response(request)
