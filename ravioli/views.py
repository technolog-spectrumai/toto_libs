from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
from .models import Note
from oya.page import PageProcessor


def public_notes_list(request):
    """
    Public notes list with optional category filter and pagination.
    """
    selected_category = request.GET.get("category", "")
    notes_qs = Note.objects.filter(is_public=True).order_by("-created_at")

    if selected_category:
        notes_qs = notes_qs.filter(category=selected_category)

    paginator = Paginator(notes_qs, 10)  # 10 notes per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories = (
        Note.objects.filter(is_public=True)
        .exclude(category__isnull=True)
        .exclude(category__exact="")
        .values_list("category", flat=True)
        .distinct()
    )

    context = {
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
        "categories": categories,
        "selected_category": selected_category,
    }
    return render(
        request,
        "ravioli/public_notes_list.html",
        PageProcessor().decorate(context, request)
    )


def public_note_detail(request, pk):
    note = get_object_or_404(Note, pk=pk, is_public=True)
    context = {"note": note}
    return render(request, "ravioli/public_note_detail.html", PageProcessor().decorate(context, request))
