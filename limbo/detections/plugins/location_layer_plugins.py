"""
Locations map-layer provider for the Detections app.
Registered in DetectionsConfig.ready().

Active detections are exported as a severity heat layer (1=low … 4=critical).
Zone/territory geometries are used directly; address Points are buffered.
"""
from __future__ import annotations

import json

_BUFFER_DEG = 0.25
_SEVERITY_VALUES = {"low": 1.0, "medium": 2.0, "high": 3.0, "critical": 4.0}
_ACTIVE_STATUSES = ["new", "acknowledged", "handling"]


def _to_polygon_geojson(geom) -> dict:
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return json.loads(geom.geojson)
    return json.loads(geom.buffer(_BUFFER_DEG).geojson)


def detections_severity_layer() -> dict | None:
    """Active detections as a severity heat layer in the Locations map."""
    try:
        from toto.detections.models import Detection

        qs = list(
            Detection.objects
            .filter(status__in=_ACTIVE_STATUSES)
            .select_related("address", "zone", "route", "category")
        )
        if not qs:
            return None

        polygons = []
        for det in qs:
            geom = det.map_geometry
            if not geom:
                continue
            val = _SEVERITY_VALUES.get(det.severity, 1.0)
            center = (
                json.loads(det.address.geometry.geojson)
                if det.address and det.address.geometry
                else None
            )
            polygons.append({
                "id": f"det-{det.pk}",
                "name": det.title,
                "value": val,
                "properties": {
                    "severity": det.severity,
                    "detection_type": det.detection_type,
                    "status": det.status,
                    "location": det.location_label,
                },
                "center": center,
                "geometry": _to_polygon_geojson(geom),
            })

        if not polygons:
            return None

        return {
            "id": "detections-severity",
            "name": "Detection Severity",
            "slug": "detections-severity",
            "description": "Active detections by severity. 1 = Low · 2 = Medium · 3 = High · 4 = Critical.",
            "unit": "",
            "min_value": 1.0,
            "max_value": 4.0,
            "style": {},
            "inverted_importance": True,
            "half_range": False,
            "polygons": polygons,
        }
    except Exception:
        import logging
        logging.getLogger("detections.layers").exception("detections_severity_layer failed")
        return None
