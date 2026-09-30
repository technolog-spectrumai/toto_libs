"""
Field Command plugins for the Locations app.
Registered in LocationsConfig.ready().
"""
import json
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# Map features
# ---------------------------------------------------------------------------

def locations_map_features(request=None):
    """The viewer's map items (map domains, 2026-09-30): what their clearances
    let them read, or with no request only the open ones."""
    from toto.locations.access import (
        readable_addresses, readable_layers, readable_routes, readable_territories, readable_zones,
    )
    from toto.locations.models import MapLayer, MapLayerPolygon

    user = getattr(request, "user", None)
    features = []
    territory_names = dict(readable_territories(user).values_list("pk", "name"))

    for t in readable_territories(user).exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(t.geometry.geojson),
            "properties": {"layer": "territory", "name": t.name, "id": t.pk},
        })

    for z in readable_zones(user).exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(z.geometry.geojson),
            "properties": {
                "layer": "zone", "name": z.name, "id": z.pk,
                "territory": territory_names.get(z.territory_id, ""),
            },
        })

    for r in readable_routes(user).exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(r.geometry.geojson),
            "properties": {"layer": "route", "name": str(r), "id": r.pk},
        })

    for a in readable_addresses(user).exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(a.geometry.geojson),
            "properties": {"layer": "address", "name": str(a), "id": a.pk},
        })

    for poly in MapLayerPolygon.objects.select_related("layer").filter(
            layer__in=readable_layers(user, MapLayer.objects.filter(is_active=True))):
        features.append({
            "type": "Feature",
            "geometry": json.loads(poly.geometry.geojson),
            "properties": {
                "layer": "map_layer",
                "layer_name": poly.layer.name,
                "layer_slug": poly.layer.slug,
                "name": poly.name,
                "value": poly.value,
                "unit": poly.layer.unit,
            },
        })

    return features


# ---------------------------------------------------------------------------
# Metrics section
# ---------------------------------------------------------------------------

def locations_metrics_section(request=None):
    """Counts of what the viewer may read — a hidden item is not counted."""
    from toto.locations.access import (
        readable_addresses, readable_routes, readable_territories, readable_zones,
    )

    user = getattr(request, "user", None)
    addr_count = readable_addresses(user).count()
    territory_count = readable_territories(user).count()
    zone_count = readable_zones(user).count()
    route_count = readable_routes(user).count()

    return {
        "key": "locations",
        "title": _("Locations"),
        "order": 10,
        "app_url": "/locations/",
        "ribbon": [
            {"label": _("Addresses"), "value": addr_count, "alert": False},
            {"label": _("Territories"), "value": territory_count, "alert": False},
        ],
        "kpis": [
            {"label": _("Addresses"), "value": addr_count, "sub": _("with geometry"), "alert": False},
            {"label": _("Territories"), "value": territory_count, "sub": _("defined"), "alert": False},
            {"label": _("Zones"), "value": zone_count, "sub": _("defined"), "alert": False},
            {"label": _("Routes"), "value": route_count, "sub": _("defined"), "alert": False},
        ],
        "chart": None,
        "table": None,
    }
