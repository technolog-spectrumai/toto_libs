from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from finance.models import Asset
from oya.page import PageProcessor


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
            "assigned_to": asset.assigned_to.display_name if asset.assigned_to else None,
            "location": str(asset.location) if asset.location else None,
            "metadata": asset.metadata or {},
            "created_at": asset.created_at,
        },
    }
    return render(
        request,
        "finance/asset_detail.html",
        PageProcessor().decorate(context, request))

