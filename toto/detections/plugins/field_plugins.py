"""
Field Command plugins for the Detections app.
Registered in DetectionsConfig.ready().
"""
import json


_ACTIVE_STATUSES = ["new", "acknowledged", "handling"]

_SEVERITY_COLOR = {
    "low": "#22c55e",
    "medium": "#f59e0b",
    "high": "#ef4444",
    "critical": "#7c3aed",
}


# ---------------------------------------------------------------------------
# Map features
# ---------------------------------------------------------------------------

def detections_map_features(request=None):
    from toto.detections.models import Detection

    features = []
    for det in (
        Detection.objects
        .filter(status__in=_ACTIVE_STATUSES)
        .select_related("address", "zone", "route", "category")
    ):
        geom = det.map_geometry
        if not geom:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom.geojson),
            "properties": {
                "layer": "detection",
                "name": det.title,
                "severity": det.severity,
                "color": _SEVERITY_COLOR.get(det.severity, "#94a3b8"),
                "status": det.status,
                "detection_type": det.detection_type,
                "location": det.location_label,
                "id": det.pk,
            },
        })
    return features


# ---------------------------------------------------------------------------
# Metrics section
# ---------------------------------------------------------------------------

def detections_metrics_section(request=None):
    from django.db.models import Count
    from toto.detections.models import Detection

    active_qs = Detection.objects.filter(status__in=_ACTIVE_STATUSES)
    active_count = active_qs.count()
    critical_count = active_qs.filter(severity="critical").count()
    high_count = active_qs.filter(severity="high").count()
    total_count = Detection.objects.count()

    sev_by_label = {
        row["severity"]: row["count"]
        for row in active_qs.values("severity").annotate(count=Count("id"))
    }
    sev_order = ["low", "medium", "high", "critical"]
    chart_labels = [s.capitalize() for s in sev_order]
    chart_data = [sev_by_label.get(s, 0) for s in sev_order]
    chart_colors = [_SEVERITY_COLOR[s] for s in sev_order]

    top_locations = list(
        active_qs
        .filter(address__isnull=False)
        .values("address__locality_name", "address__country_name")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    table = None
    if top_locations:
        table = {
            "headers": ["Location", "Country", "Active"],
            "rows": [
                [
                    row["address__locality_name"] or "—",
                    row["address__country_name"] or "—",
                    row["count"],
                ]
                for row in top_locations
            ],
        }

    return {
        "key": "detections",
        "title": "Detections",
        "order": 30,
        "app_url": "/detections/",
        "ribbon": [
            {"label": "Active", "value": active_count, "alert": active_count > 0},
            {"label": "Critical", "value": critical_count, "alert": critical_count > 0},
        ],
        "kpis": [
            {"label": "Active Detections", "value": active_count, "sub": "new / ack / handling", "alert": active_count > 0},
            {"label": "Critical", "value": critical_count, "sub": "highest priority", "alert": critical_count > 0},
            {"label": "High Severity", "value": high_count, "sub": "need attention", "alert": high_count > 0},
            {"label": "Total Detections", "value": total_count, "sub": "all time", "alert": False},
        ],
        "chart": {
            "type": "doughnut",
            "labels": chart_labels,
            "data": chart_data,
            "colors": chart_colors,
        },
        "table": table,
    }
