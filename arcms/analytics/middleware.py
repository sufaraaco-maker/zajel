"""طلب القياس (/a/v) قبل بقية الوسطاء: يأتي مع كل قراءة، ولا يحتاج جلسة ولا دخولاً ولا رسائل ولا CSRF
(معفى منه أصلاً). في زحام خبر عاجل هو أكثر ما يصل التطبيقَ بعد الصفحات المخزنة."""

from __future__ import annotations

BEACON_PATH = "/a/v"


class BeaconMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path_info == BEACON_PATH and not _tips_host(request):
            from arcms.public.views import beacon

            return beacon(request)
        return self.get_response(request)


def _tips_host(request) -> bool:
    """نطاق صندوق المعلومات المستقل لا يقيس شيئاً (يتولاه TipsHostMiddleware بعدنا)."""
    from django.conf import settings

    tips = settings.ARCMS_TIPS_HOST
    return bool(tips) and request.get_host().lower().rsplit(":", 1)[0] == tips
