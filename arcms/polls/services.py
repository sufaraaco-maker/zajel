"""التصويت وعرض الاستطلاع داخل المواد والصفحة الرئيسية."""

from __future__ import annotations

import re

from django.db import transaction
from django.db.models import F
from django.template.loader import render_to_string

from .models import Poll, PollOption

SHORTCODE = re.compile(r"(?:<p>\s*)?\[poll:(\d{1,9})\](?:\s*</p>)?")
COOKIE = "arcms_poll_{}"


def cookie_name(poll_id: int) -> str:
    return COOKIE.format(poll_id)


def voted(request, poll: Poll) -> bool:
    return request.COOKIES.get(cookie_name(poll.pk)) == "1"


def record_vote(poll: Poll, option_id: int) -> bool:
    """يزيد العدّادين في معاملة واحدة. يعيد False إن كان الخيار لا يتبع الاستطلاع أو أُغلق."""
    if not poll.is_active:
        return False
    with transaction.atomic():
        updated = PollOption.objects.filter(pk=option_id, poll=poll).update(votes=F("votes") + 1)
        if not updated:
            return False
        Poll.objects.filter(pk=poll.pk).update(total_votes=F("total_votes") + 1)
    return True


def render_poll(poll: Poll, request=None) -> str:
    show = not poll.is_active or poll.show_results_before_vote or (request is not None and voted(request, poll))
    return render_to_string("public/partials/poll.html", {"poll": poll, "results": poll.results(), "show_results": show,
                                                          "request": request})


def render_shortcodes(html: str, request=None) -> str:
    """يستبدل [poll:12] في متن المادة بالاستطلاع نفسه؛ الرقم غير الموجود يُحذف بصمت."""
    ids = {int(m) for m in SHORTCODE.findall(html or "")}
    if not ids:
        return html
    polls = {p.pk: p for p in Poll.objects.filter(pk__in=ids).prefetch_related("options")}

    def repl(m: re.Match) -> str:
        poll = polls.get(int(m.group(1)))
        return render_poll(poll, request) if poll else ""

    return SHORTCODE.sub(repl, html)
