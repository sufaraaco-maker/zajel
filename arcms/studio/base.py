"""أدوات مشتركة لعروض غرفة التحرير: التحقق من الصلاحية، وعروض القوائم والنماذج العامة."""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST


def requires(*caps: str):
    """يسمح بالدخول لمن يملك إحدى الصلاحيات المذكورة."""

    def deco(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if caps and not any(request.user.can(c) for c in caps):
                raise PermissionDenied("لا تملك صلاحية الوصول إلى هذه الصفحة.")
            return view(request, *args, **kwargs)

        return wrapper

    return deco


def paginate(request, qs, size: int = 30):
    try:
        number = int(request.GET.get("page", 1))
    except ValueError:
        number = 1
    return Paginator(qs, size).get_page(number)


class Crud:
    """قائمة + إنشاء + تعديل + حذف لنموذج بسيط، بقوالب عامة."""

    def __init__(self, *, model, form, cap, name: str, title: str, singular: str, columns: list[tuple[str, str]],
                 queryset=None, search_field: str | None = None, template_list="studio/crud_list.html",
                 template_form="studio/crud_form.html", deletable=True, public_url: bool = True, help_text: str = "",
                 list_extra=None, form_extra=None, form_takes_user: bool = False):
        self.model = model
        self.form = form
        self.cap = cap
        self.name = name
        self.title = title
        self.singular = singular
        self.columns = columns
        self.queryset = queryset
        self.search_field = search_field
        self.template_list = template_list
        self.template_form = template_form
        self.deletable = deletable
        self.public_url = public_url
        self.help_text = help_text
        # دوال اختيارية تضيف سياقاً للقائمة أو النموذج: (request) و(request, obj).
        self.list_extra = list_extra
        self.form_extra = form_extra
        self.form_takes_user = form_takes_user

    def qs(self):
        return self.queryset() if callable(self.queryset) else self.model.objects.all()

    def ctx(self, **extra):
        base = {
            "crud": self,
            "list_url": reverse(f"studio:{self.name}_list"),
            "new_url": reverse(f"studio:{self.name}_new"),
        }
        base.update(extra)
        return base

    def views(self):
        crud = self

        @requires(crud.cap)
        def list_view(request):
            qs = crud.qs()
            q = request.GET.get("q", "").strip()
            if q and crud.search_field:
                qs = qs.filter(**{f"{crud.search_field}__icontains": q})
            page = paginate(request, qs, 50)
            rows = [
                {
                    "obj": obj,
                    "cells": [_cell(obj, attr) for attr, _ in crud.columns],
                    "edit_url": reverse(f"studio:{crud.name}_edit", args=[obj.pk]),
                }
                for obj in page
            ]
            extra = crud.list_extra(request) if crud.list_extra else {}
            return render(request, crud.template_list, crud.ctx(page=page, rows=rows, q=q, **extra))

        @requires(crud.cap)
        def form_view(request, pk=None):
            obj = get_object_or_404(crud.model, pk=pk) if pk else None
            kwargs = {"user": request.user} if crud.form_takes_user else {}
            form = crud.form(request.POST or None, request.FILES or None, instance=obj, **kwargs)
            if request.method == "POST" and form.is_valid():
                saved = form.save()
                messages.success(request, f"حُفظ {crud.singular} «{saved}».")
                if "save_add" in request.POST:
                    return redirect(f"studio:{crud.name}_new")
                return redirect(f"studio:{crud.name}_list")
            extra = crud.form_extra(request, obj) if crud.form_extra else {}
            return render(request, crud.template_form, crud.ctx(form=form, obj=obj, **extra))

        @requires(crud.cap)
        @require_POST
        def delete_view(request, pk):
            if not crud.deletable:
                raise PermissionDenied
            obj = get_object_or_404(crud.model, pk=pk)
            from django.db.models import ProtectedError

            try:
                obj.delete()
                messages.success(request, f"حُذف {crud.singular}.")
            except ProtectedError:
                messages.error(request, f"لا يمكن حذف {crud.singular} لارتباطه بمواد منشورة. عطّله بدلاً من ذلك.")
            return redirect(f"studio:{crud.name}_list")

        return list_view, form_view, delete_view

    def urls(self):
        from django.urls import path

        list_view, form_view, delete_view = self.views()
        return [
            path(f"{self.name}/", list_view, name=f"{self.name}_list"),
            path(f"{self.name}/new/", form_view, name=f"{self.name}_new"),
            path(f"{self.name}/<int:pk>/", form_view, name=f"{self.name}_edit"),
            path(f"{self.name}/<int:pk>/delete/", delete_view, name=f"{self.name}_delete"),
        ]


def _cell(obj, attr: str):
    value = obj
    for part in attr.split("."):
        value = getattr(value, part, None)
        if callable(value):
            value = value()
    if isinstance(value, bool):
        return "✓" if value else "—"
    return "" if value is None else value


def forbid_unless(condition: bool, message: str = "لا تملك صلاحية هذا الإجراء."):
    if not condition:
        raise PermissionDenied(message)
