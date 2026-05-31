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
    from toto.locations.models import Address, Territory, Zone, Route, MapLayer, MapLayerPolygon
    features = []

    for t in Territory.objects.exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(t.geometry.geojson),
            "properties": {"layer": "territory", "name": t.name, "id": t.pk},
        })

    for z in Zone.objects.exclude(geometry=None).select_related("territory"):
        features.append({
            "type": "Feature",
            "geometry": json.loads(z.geometry.geojson),
            "properties": {
                "layer": "zone", "name": z.name, "id": z.pk,
                "territory": z.territory.name if z.territory else "",
            },
        })

    for r in Route.objects.exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(r.geometry.geojson),
            "properties": {"layer": "route", "name": str(r), "id": r.pk},
        })

    for a in Address.objects.exclude(geometry=None):
        features.append({
            "type": "Feature",
            "geometry": json.loads(a.geometry.geojson),
            "properties": {"layer": "address", "name": str(a), "id": a.pk},
        })

    for poly in MapLayerPolygon.objects.select_related("layer").filter(layer__is_active=True):
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
    from toto.locations.models import Address, Territory, Zone, Route

    addr_count = Address.objects.count()
    territory_count = Territory.objects.count()
    zone_count = Zone.objects.count()
    route_count = Route.objects.count()

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
