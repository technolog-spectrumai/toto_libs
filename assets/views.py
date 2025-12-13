from django.core.paginator import Paginator
from django.shortcuts import render, get_object_or_404
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


PageProcessor


@login_required
def asset_detail(request, pk):
    """
    Detail view for a single active asset.
    """
    asset = get_object_or_404(
        Asset.objects.select_related("asset_type", "assigned_to", "location"),
        pk=pk,
        is_active=True,  # only allow active assets
    )

    context = {
        "asset": asset,
        "asset_data": {
            "id": asset.id,
            "name": asset.name,
            "type": asset.asset_type.name if asset.asset_type else None,
            "description": asset.description,
            "serial_number": asset.serial_number,
            "purchase_date": asset.purchase_date,
            "purchase_price": asset.purchase_price,
            "assigned_to": asset.assigned_to.username if asset.assigned_to else None,
            "location": str(asset.location) if asset.location else None,
            "metadata": asset.metadata or {},
            "created_at": asset.created_at,
        },
    }
    return render(
        request,
        "assets/asset_detail.html",
        PageProcessor().decorate(context, request))

