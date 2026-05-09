import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from toto.core.page import PageProcessor
from .models import (
    Territory,
    Zone,
    RouteChain,
    Route,
    Address
)


def geometry_json(geometry):
    return json.loads(geometry.geojson) if geometry else None


def route_chain_geometry(route_chain):
    coordinates = []

    for route in route_chain.routes.order_by("sequence", "name", "pk"):
        geometry = geometry_json(route.geometry)
        if not geometry:
            continue

        if geometry["type"] == "MultiLineString":
            coordinates.extend(geometry["coordinates"])
        elif geometry["type"] == "LineString":
            coordinates.append(geometry["coordinates"])

    if not coordinates:
        return None

    return {
        "type": "MultiLineString",
        "coordinates": coordinates,
    }


@login_required
def locations_all(request):
    locations = []

    # Territories (polygons)
    for t in Territory.objects.all():
        locations.append({
            "type": "Territory",
            "name": t.name or f"Territory {t.pk}",
            "detail": f"Capital: {t.capital}" if t.capital else "Territory",
            "geometry": geometry_json(t.geometry),
            "geometry_json": geometry_json(t.geometry),
        })

    # Zones (multipolygons)
    for z in Zone.objects.all():
        locations.append({
            "type": "Zone",
            "name": z.name or f"Zone {z.pk}",
            "detail": f"Inside {z.territory.name}" if z.territory else "Standalone zone",
            "geometry": geometry_json(z.geometry),
            "geometry_json": geometry_json(z.geometry),
        })

    # Route chains (combined multilines)
    for chain in RouteChain.objects.prefetch_related("routes").all():
        geometry = route_chain_geometry(chain)
        locations.append({
            "type": "Route Chain",
            "name": chain.name or f"Route Chain {chain.pk}",
            "detail": chain.description or f"{chain.routes.count()} routes",
            "geometry": geometry,
            "geometry_json": geometry,
        })

    # Routes (multiline)
    for r in Route.objects.select_related("route_chain").all():
        locations.append({
            "type": "Route",
            "name": r.name or f"Route {r.pk}",
            "detail": f"In {r.route_chain.name}" if r.route_chain else "Route",
            "geometry": geometry_json(r.geometry),
            "geometry_json": geometry_json(r.geometry),
        })

    # Addresses (points)
    for addr in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(addr),
            "detail": addr.locality_name,
            "geometry": geometry_json(addr.geometry),
            "geometry_json": geometry_json(addr.geometry),
        })

    context = {
        "locations": locations,
        "locations_json": json.dumps(locations),
    }

    return render(
        request,
        "locations/locations.html",
        PageProcessor().decorate(context, request)
    )
