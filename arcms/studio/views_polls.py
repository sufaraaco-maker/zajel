"""استطلاعات القرّاء في غرفة التحرير: إنشاؤها ونتائجها ورمز تضمينها في المواد."""

from __future__ import annotations

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from arcms.accounts.roles import Cap
from arcms.polls.models import Poll

from .base import paginate, requires
from .forms import PollForm


@requires(Cap.POLLS)
def poll_list(request):
    qs = Poll.objects.prefetch_related("options")
    return render(request, "studio/polls.html", {"page": paginate(request, qs, 20)})


@requires(Cap.POLLS)
def poll_edit(request, pk: int | None = None):
    poll = get_object_or_404(Poll, pk=pk) if pk else None
    form = PollForm(request.POST or None, instance=poll)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            obj = form.save(commit=False)
            if not obj.pk:
                obj.created_by = request.user
            obj.save()
            form.sync_options(obj)
        messages.success(request, f"حُفظ الاستطلاع. ضع {obj.shortcode} في متن أي مادة لعرضه فيها.")
        return redirect("studio:polls")
    return render(request, "studio/poll_form.html", {"form": form, "obj": poll})


@requires(Cap.POLLS)
@require_POST
def poll_delete(request, pk: int):
    poll = get_object_or_404(Poll, pk=pk)
    poll.delete()
    messages.success(request, "حُذف الاستطلاع. أي رمز تضمين له في المواد لن يظهر شيئاً.")
    return redirect("studio:polls")
