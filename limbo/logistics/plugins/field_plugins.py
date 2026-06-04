"""
Field Command plugins for the Logistics app.
Registered in LogisticsConfig.ready().
"""
import json
from django.utils.translation import gettext_lazy as _


_IN_TRANSIT = ["picked_up", "in_transit", "at_hub", "out_for_delivery"]


# ---------------------------------------------------------------------------
# Map features
# ---------------------------------------------------------------------------

def logistics_map_features(request=None):
    from toto.logistics.models import Transport

    features = []
    for t in Transport.objects.filter(
        is_active=True, current_location__geometry__isnull=False
    ).select_related("current_location"):
        features.append({
            "type": "Feature",
            "geometry": json.loads(t.current_location.geometry.geojson),
            "properties": {
                "layer": "transport",
                "name": str(t),
                "mode": t.mode,
                "id": t.pk,
            },
        })
    return features


# ---------------------------------------------------------------------------
# Metrics section
# ---------------------------------------------------------------------------

def logistics_metrics_section(request=None):
    from django.db.models import Count
    from toto.logistics.models import Package, Transport, PackageStatus

    pkg_by_status = {
        row["status"]: row["count"]
        for row in Package.objects.values("status").annotate(count=Count("id"))
    }
    in_transit = sum(pkg_by_status.get(s, 0) for s in _IN_TRANSIT)
    failed = pkg_by_status.get("failed", 0)
    active_transports = Transport.objects.filter(is_active=True).count()

    chart_labels = [label for _, label in Package._meta.get_field("status").choices]
    chart_data = [pkg_by_status.get(val, 0) for val, _ in Package._meta.get_field("status").choices]

    return {
        "key": "logistics",
        "title": _("Logistics"),
        "order": 40,
        "app_url": "/logistics/",
        "ribbon": [
            {"label": _("In Transit"), "value": in_transit, "alert": False},
            {"label": _("Failed"), "value": failed, "alert": failed > 0},
        ],
        "kpis": [
            {"label": _("Packages in Transit"), "value": in_transit, "sub": _("active"), "alert": False},
            {"label": _("Failed Packages"), "value": failed, "sub": _("need attention"), "alert": failed > 0},
            {"label": _("Active Transports"), "value": active_transports, "sub": _("vehicles"), "alert": False},
            {"label": _("Total Packages"), "value": sum(pkg_by_status.values()), "sub": _("all time"), "alert": False},
        ],
        "chart": {
            "type": "bar",
            "labels": chart_labels,
            "data": chart_data,
        },
        "table": None,
    }
