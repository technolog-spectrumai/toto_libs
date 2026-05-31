"""
Field Command plugins for the Inventory app.
Registered in InventoryConfig.ready().
"""
import json
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# Map features
# ---------------------------------------------------------------------------

def inventory_map_features(request=None):
    from toto.inventory.models import InventorySite

    features = []
    for site in (
        InventorySite.objects
        .filter(is_active=True, address__geometry__isnull=False)
        .select_related("address")
    ):
        features.append({
            "type": "Feature",
            "geometry": json.loads(site.address.geometry.geojson),
            "properties": {
                "layer": "inventory_site",
                "name": site.name,
                "site_type": site.site_type,
                "slug": site.slug,
                "id": site.pk,
            },
        })
    return features


# ---------------------------------------------------------------------------
# Metrics section
# ---------------------------------------------------------------------------

def inventory_metrics_section(request=None):
    from django.db.models import Count
    from toto.inventory.models import InventorySite, RealWorldObject

    total_sites = InventorySite.objects.count()
    active_sites = InventorySite.objects.filter(is_active=True).count()
    total_objects = RealWorldObject.objects.count()

    sites_by_type = {
        row["site_type"]: row["count"]
        for row in InventorySite.objects.values("site_type").annotate(count=Count("id"))
    }
    chart_labels = [k or "unspecified" for k in sites_by_type]
    chart_data = list(sites_by_type.values())

    return {
        "key": "inventory",
        "title": _("Inventory"),
        "order": 50,
        "app_url": "/inventory/",
        "ribbon": [
            {"label": _("Active Sites"), "value": active_sites, "alert": False},
            {"label": _("Objects"), "value": total_objects, "alert": False},
        ],
        "kpis": [
            {"label": _("Active Sites"), "value": active_sites, "sub": _("operational"), "alert": False},
            {"label": _("Total Sites"), "value": total_sites, "sub": _("all time"), "alert": False},
            {"label": _("Tracked Objects"), "value": total_objects, "sub": _("inventory items"), "alert": False},
        ],
        "chart": {
            "type": "doughnut",
            "labels": chart_labels,
            "data": chart_data,
        } if chart_labels else None,
        "table": None,
    }
