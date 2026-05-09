import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

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


ROUTING_MODES = {
    "car": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
    "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
}


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


def parse_coordinate(value, label, minimum, maximum):
    if value in (None, ""):
        raise ValueError(f"{label} is required.")

    try:
        coordinate = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a number.") from exc

    if coordinate < minimum or coordinate > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")

    return coordinate


def fetch_traversable_route(start_lng, start_lat, end_lng, end_lat, mode):
    endpoint = ROUTING_MODES[mode]
    coordinates = f"{start_lng},{start_lat};{end_lng},{end_lat}"
    query = urlencode({
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
        "alternatives": "false",
    })
    url = f"{endpoint}/{coordinates}?{query}"

    try:
        with urlopen(url, timeout=12) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise ValueError(f"Routing service returned HTTP {exc.code}.") from exc
    except URLError as exc:
        raise ValueError("Routing service is unavailable right now.") from exc
    except TimeoutError as exc:
        raise ValueError("Routing service timed out.") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        message = payload.get("message") or "No traversable route found."
        raise ValueError(message)

    route = payload["routes"][0]
    return {
        "type": "Feature",
        "properties": {
            "mode": mode,
            "distance_km": round(route.get("distance", 0) / 1000, 2),
            "duration_min": round(route.get("duration", 0) / 60, 1),
        },
        "geometry": route["geometry"],
    }


@login_required
def route_search(request):
    form = {
        "mode": request.GET.get("mode", "car"),
        "start_lat": request.GET.get("start_lat", "54.3487"),
        "start_lng": request.GET.get("start_lng", "18.6538"),
        "end_lat": request.GET.get("end_lat", "54.4067"),
        "end_lng": request.GET.get("end_lng", "18.6717"),
    }
    route = None
    error = ""

    if request.GET:
        try:
            if form["mode"] not in ROUTING_MODES:
                raise ValueError("Mode must be car or foot.")

            start_lat = parse_coordinate(form["start_lat"], "Start latitude", -90, 90)
            start_lng = parse_coordinate(form["start_lng"], "Start longitude", -180, 180)
            end_lat = parse_coordinate(form["end_lat"], "End latitude", -90, 90)
            end_lng = parse_coordinate(form["end_lng"], "End longitude", -180, 180)
            route = fetch_traversable_route(start_lng, start_lat, end_lng, end_lat, form["mode"])
        except ValueError as exc:
            error = str(exc)

    context = {
        "form": form,
        "route": route,
        "route_json": json.dumps(route),
        "error": error,
    }

    return render(
        request,
        "locations/route_search.html",
        PageProcessor().decorate(context, request),
    )
