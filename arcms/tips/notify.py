"""تنبيه الطاقم بوصول بلاغ دون أي تفصيل من محتواه."""

from __future__ import annotations

from django.db import transaction


def notify_new_tip(tip) -> None:
    from arcms.accounts.notify import new_tip

    transaction.on_commit(new_tip)
