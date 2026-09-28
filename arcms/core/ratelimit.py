"""حدود معدّل بسيطة فوق ذاكرة Django المؤقتة.

المفتاح بصمة HMAC للمصدر (عنوان IP أو بريد) لا القيمة نفسها، فلا يُحفظ عنوان
قارئ في الذاكرة المؤقتة حتى لمدة النافذة.
"""

from __future__ import annotations

import hashlib
import hmac

from django.conf import settings
from django.core.cache import cache


def _key(scope: str, ident: str) -> str:
    digest = hmac.new(settings.SECRET_KEY.encode(), f"{scope}:{ident}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"arcms:rl:{scope}:{digest}"


def exceeded(scope: str, ident: str | None, limit: int, window: int) -> bool:
    """يسجّل محاولة ويعيد True إن تجاوز المصدر `limit` محاولة خلال `window` ثانية."""
    if not ident:
        return False
    key = _key(scope, ident)
    if cache.add(key, 1, window):
        return False
    try:
        count = cache.incr(key)
    except ValueError:  # انتهت صلاحية المفتاح بين الخطوتين
        cache.set(key, 1, window)
        return False
    return count > limit
