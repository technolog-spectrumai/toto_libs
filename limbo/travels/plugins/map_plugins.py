import json

from django.urls import reverse

from toto.locations.plugins.map_plugins import LocationMapPlugin


def _geometry_json(geometry):
    return json.loads(geometry.geojson) if geometry else None


def _travel_items():
    from toto.travels.models import Travel

    items = []
    for travel in Travel.objects.select_related(
        "route__start_address",
        "route__end_address",
    ).filter(route__geometry__isnull=False):
        route = travel.route
        start = str(route.start_address) if route.start_address else None
        end = str(route.end_address) if route.end_address else None
        detail = f"{start} → {end}" if start and end else route.name or f"Route {route.pk}"
        items.append({
            "type": "Travel",
            "name": str(travel),
            "detail": detail,
            "geometry": _geometry_json(route.geometry),
            "geometry_json": _geometry_json(route.geometry),
            "detail_url": reverse("travels:travel_review", args=[travel.pk]),
        })
    return items


def _visit_items():
    from toto.travels.models import Visit

    items = []
    for visit in Visit.objects.select_related(
        "participant",
        "location",
    ).filter(location__geometry__isnull=False):
        address = visit.location
        items.append({
            "type": "Visit",
            "name": str(address),
            "detail": str(visit.participant),
            "geometry": _geometry_json(address.geometry),
            "geometry_json": _geometry_json(address.geometry),
            "detail_url": reverse("travels:visit_review", args=[address.pk]),
        })
    return items


LocationMapPlugin.register(_travel_items)
LocationMapPlugin.register(_visit_items)
