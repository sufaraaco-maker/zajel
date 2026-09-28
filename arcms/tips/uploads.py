from django.core.files.uploadhandler import MemoryFileUploadHandler

from .services import MAX_REQUEST_BYTES


class InMemoryOnlyUploadHandler(MemoryFileUploadHandler):
    """يبقي مرفقات المصادر في الذاكرة: لا تُكتب نسخة صريحة منها على قرص الخادم
    ولو مؤقتاً قبل نزع بياناتها وتشفيرها. الطلب الأكبر من الحد يُرفض في العرض."""

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        self.activated = content_length <= MAX_REQUEST_BYTES
