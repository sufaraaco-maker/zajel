"""حدود معدّل بسيطة فوق ذاكرة Django المؤقتة.

المفتاح بصمة HMAC للمصدر (عنوان IP أو بريد) لا القيمة نفسها، فلا يُحفظ عنوان
قارئ في الذاكرة المؤقتة حتى لمدة النافذة.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time

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


_local_counts: dict[tuple, int] = {}
_local_lock = threading.Lock()


def exceeded_local(scope: str, ident: str | None, limit: int, window: int) -> bool:
    """مثل exceeded لكن في ذاكرة العملية وحدها: بلا طلب إلى المخزن المشترك ولا القاعدة.

    لما يتكرر آلاف المرات في الثانية ويكفيه التقريب (طلب القياس): الحد لكل عملية، فالسقف الفعلي
    الحدُّ مضروباً في عدد العمليات. النوافذ ثابتة، والقديم منها يُحذف كلما امتلأ الجدول.
    """
    if not ident:
        return False
    bucket = int(time.time() // window)
    key = (scope, _key(scope, ident), bucket)
    with _local_lock:
        if len(_local_counts) > 100_000:
            for old in [k for k in _local_counts if k[2] != bucket]:
                del _local_counts[old]
            if len(_local_counts) > 100_000:
                _local_counts.clear()
        count = _local_counts.get(key, 0) + 1
        _local_counts[key] = count
    return count > limit


def clear_local() -> None:
    with _local_lock:
        _local_counts.clear()
