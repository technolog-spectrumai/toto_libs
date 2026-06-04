"""Field Command plugins for the Incidents app. Registered in IncidentsConfig.ready()."""
import json
from django.utils.translation import gettext_lazy as _

_OPEN_STATUSES = ["open", "contained"]

_SEVERITY_COLOR = {
    "low": "#22c55e",
    "medium": "#f59e0b",
    "high": "#ef4444",
    "critical": "#7c3aed",
}


def incidents_map_features(request=None):
    from toto.incidents.models import Incident

    features = []
    for inc in (
        Incident.objects
        .filter(status__in=_OPEN_STATUSES)
        .select_related("address", "zone", "category")
    ):
        geom = inc.map_geometry
        if not geom:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(geom.geojson),
            "properties": {
                "layer": "incident",
                "name": inc.title,
                "severity": inc.severity,
                "color": _SEVERITY_COLOR.get(inc.severity, "#94a3b8"),
                "status": inc.status,
                "incident_type": inc.incident_type,
                "location": inc.location_label,
                "id": str(inc.pk),
            },
        })
    return features


def incidents_metrics_section(request=None):
    from django.db.models import Count
    from toto.incidents.models import Incident

    open_qs = Incident.objects.filter(status__in=_OPEN_STATUSES)
    open_count = open_qs.count()
    critical_count = open_qs.filter(severity="critical").count()
    high_count = open_qs.filter(severity="high").count()
    total_count = Incident.objects.count()

    sev_by_label = {
        row["severity"]: row["count"]
        for row in open_qs.values("severity").annotate(count=Count("id"))
    }
    sev_order = ["low", "medium", "high", "critical"]
    chart_colors = [_SEVERITY_COLOR[s] for s in sev_order]

    return {
        "key": "incidents",
        "title": _("Incidents"),
        "order": 25,
        "app_url": "/incidents/",
        "ribbon": [
            {"label": _("Open"), "value": open_count, "alert": open_count > 0},
            {"label": _("Critical"), "value": critical_count, "alert": critical_count > 0},
        ],
        "kpis": [
            {"label": _("Open Incidents"), "value": open_count, "sub": _("open / contained"), "alert": open_count > 0},
            {"label": _("Critical"), "value": critical_count, "sub": _("highest priority"), "alert": critical_count > 0},
            {"label": _("High Severity"), "value": high_count, "sub": _("need attention"), "alert": high_count > 0},
            {"label": _("Total Incidents"), "value": total_count, "sub": _("all time"), "alert": False},
        ],
        "chart": {
            "type": "doughnut",
            "labels": [s.capitalize() for s in sev_order],
            "data": [sev_by_label.get(s, 0) for s in sev_order],
            "colors": chart_colors,
        },
        "table": None,
    }
