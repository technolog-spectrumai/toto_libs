from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404
from django.urls import reverse
from .models import Asset
from oya.page import PageProcessor


# ---------------------------------------------------------
#  ASSET DETAIL VIEW
# ---------------------------------------------------------

@login_required
def asset_detail(request, pk):
    """
    Detail view for a single active asset.
    """
    asset = get_object_or_404(
        Asset.objects.select_related(
            "asset_type",
            "assigned_to",
            "location"
        ).prefetch_related("images", "fractional_owners__owner"),
        pk=pk,
        is_active=True,
    )

    assigned_to_name = asset.assigned_to.name if asset.assigned_to else None

    # Sorted images + fallback
    images = [
        {
            "url": img.image.url,
            "caption": img.caption,
            "order": img.order,
        }
        for img in asset.images.all().order_by("order")
    ]

    if not images:
        images = [
            {
                "url": "/static/img/placeholder.png",
                "caption": "No image available",
                "order": 0,
            }
        ]

    # Fractional ownership data
    ownership = [
        {
            "owner_name": fo.owner.name,
            "owner_type": fo.owner.profile_type,
            "percentage": fo.percentage,
        }
        for fo in asset.fractional_owners.all().select_related("owner")
    ]

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
            "assigned_to": assigned_to_name,
            "location": str(asset.location) if asset.location else None,
            "metadata": asset.metadata or {},
            "created_at": asset.created_at,
            "images": images,
            "ownership": ownership,
        },
    }

    return render(
        request,
        "assets/asset_detail.html",
        PageProcessor().decorate(context, request)
    )



# ---------------------------------------------------------
#  ASSET LIST / DASHBOARD VIEW
# ---------------------------------------------------------

@login_required
def asset_list(request):
    """
    Asset dashboard showing all active assets in a table.
    """
    assets = (
        Asset.objects.filter(is_active=True)
        .select_related("asset_type", "assigned_to", "location")
        .order_by("name")
    )

    # Table column headers
    assets_columns = [
        {"label": "Name"},
        {"label": "Type"},
        {"label": "Assigned To"},
        {"label": "Location"},
        {"label": "Created"},
    ]

    # Convert queryset into the row structure your template expects
    assets_rows = []
    for asset in assets:
        assets_rows.append([
            {
                "type": "link",
                "text": asset.name,
                "url": reverse("assets:asset_detail", args=[asset.pk]),
            },
            {
                "type": "text",
                "text": str(asset.asset_type) if asset.asset_type else "—",
            },
            {
                "type": "text",
                "text": str(asset.assigned_to) if asset.assigned_to else "—",
            },
            {
                "type": "text",
                "text": str(asset.location) if asset.location else "—",
            },
            {
                "type": "text",
                "text": asset.created_at.strftime("%Y-%m-%d"),
            },
        ])

    context = {
        "assets_columns": assets_columns,
        "assets_rows": assets_rows,
    }

    return render(
        request,
        "assets/asset_list.html",   # ← matches your new template
        PageProcessor().decorate(context, request)
    )
