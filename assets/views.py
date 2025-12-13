from django.core.paginator import Paginator
from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from .models import Asset
from oya.page import PageProcessor


@login_required
def assets_list(request):
    """
    List only active assets with pagination.
    """
    assets_qs = Asset.objects.filter(is_active=True).select_related("asset_type", "assigned_to").order_by("-created_at")

    paginator = Paginator(assets_qs, 10)  # 10 assets per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        "page_obj": page_obj,
        "is_paginated": page_obj.has_other_pages(),
    }
    return render(
        request,
        "assets/asset_list.html",
        PageProcessor().decorate(context, request)
    )

