import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from toto.core.page import PageProcessor
from .models import (
    MapLayer,
    Territory,
    Zone,
    RouteChain,
    Route,
    Address
)


ROUTING_MODE_OPTIONS = (
    {"value": "car", "label": "Car", "icon": "fa-car-side"},
    {"value": "bicycle", "label": "Bicycle", "icon": "fa-bicycle"},
    {"value": "foot", "label": "Foot", "icon": "fa-person-walking"},
    {"value": "public_transport", "label": "Public transport", "icon": "fa-train-subway"},
)

ROAD_ROUTING_ENDPOINTS = {
    "car": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
    "bicycle": "https://routing.openstreetmap.de/routed-bike/route/v1/driving",
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


def map_layer_payload(layer):
    return {
        "id": layer.pk,
        "name": layer.name,
        "slug": layer.slug,
        "description": layer.description,
        "unit": layer.unit,
        "min_value": layer.min_value,
        "max_value": layer.max_value,
        "style": layer.style or {},
        "inverted_importance": layer.inverted_importance,
        "half_range": layer.half_range,
        "polygons": [
            {
                "id": polygon.pk,
                "name": polygon.name or f"{layer.name} polygon {polygon.pk}",
                "value": polygon.value,
                "properties": polygon.properties or {},
                "center": geometry_json(polygon.center),
                "geometry": geometry_json(polygon.geometry),
            }
            for polygon in layer.polygons.all()
            if polygon.geometry
        ],
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
        "map_layers_json": json.dumps([
            map_layer_payload(layer)
            for layer in MapLayer.objects.filter(is_active=True).prefetch_related("polygons").order_by("name")
        ]),
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
    endpoint = ROAD_ROUTING_ENDPOINTS.get(mode)
    if mode == "public_transport":
        endpoint = getattr(settings, "LOCATIONS_PUBLIC_TRANSPORT_ROUTING_URL", "")
        if not endpoint:
            raise ValueError(
                "Public transport routing needs a transit backend. "
                "Configure LOCATIONS_PUBLIC_TRANSPORT_ROUTING_URL with a compatible routing endpoint."
            )

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


def address_coordinates(address):
    return address.geometry.x, address.geometry.y


def selected_address_coordinates(address_id, label):
    if not address_id:
        return None

    try:
        address = Address.objects.get(pk=address_id, geometry__isnull=False)
    except (Address.DoesNotExist, ValueError):
        raise ValueError(f"{label} address is not available.")

    return address_coordinates(address)


@login_required
def route_search(request):
    addresses = list(
        Address.objects
        .filter(geometry__isnull=False)
        .order_by("country_name", "locality_name", "street", "building")
    )
    default_start = addresses[0] if addresses else None
    default_end = addresses[1] if len(addresses) > 1 else default_start

    form = {
        "mode": request.GET.get("mode", "car"),
        "start_address": request.GET.get("start_address", str(default_start.pk) if default_start else ""),
        "end_address": request.GET.get("end_address", str(default_end.pk) if default_end else ""),
        "start_lat": request.GET.get("start_lat", str(default_start.geometry.y) if default_start else "54.3487"),
        "start_lng": request.GET.get("start_lng", str(default_start.geometry.x) if default_start else "18.6538"),
        "end_lat": request.GET.get("end_lat", str(default_end.geometry.y) if default_end else "54.4067"),
        "end_lng": request.GET.get("end_lng", str(default_end.geometry.x) if default_end else "18.6717"),
    }
    route = None
    error = ""

    if request.GET:
        try:
            if form["mode"] not in {mode["value"] for mode in ROUTING_MODE_OPTIONS}:
                raise ValueError("Mode must be car, bicycle, foot, or public transport.")

            start_selected = selected_address_coordinates(form["start_address"], "Start")
            end_selected = selected_address_coordinates(form["end_address"], "End")

            if start_selected:
                start_lng, start_lat = start_selected
                form["start_lng"] = str(start_lng)
                form["start_lat"] = str(start_lat)
            else:
                start_lat = parse_coordinate(form["start_lat"], "Start latitude", -90, 90)
                start_lng = parse_coordinate(form["start_lng"], "Start longitude", -180, 180)

            if end_selected:
                end_lng, end_lat = end_selected
                form["end_lng"] = str(end_lng)
                form["end_lat"] = str(end_lat)
            else:
                end_lat = parse_coordinate(form["end_lat"], "End latitude", -90, 90)
                end_lng = parse_coordinate(form["end_lng"], "End longitude", -180, 180)

            route = fetch_traversable_route(start_lng, start_lat, end_lng, end_lat, form["mode"])
        except ValueError as exc:
            error = str(exc)

    address_options = [
        {
            "id": str(address.pk),
            "label": str(address),
            "latitude": address.geometry.y,
            "longitude": address.geometry.x,
            "selected_start": str(address.pk) == form["start_address"],
            "selected_end": str(address.pk) == form["end_address"],
        }
        for address in addresses
    ]
    selected_mode = next(
        mode for mode in ROUTING_MODE_OPTIONS
        if mode["value"] == form["mode"]
    )

    context = {
        "form": form,
        "address_options": address_options,
        "mode_options": ROUTING_MODE_OPTIONS,
        "selected_mode": selected_mode,
        "route": route,
        "route_json": json.dumps(route),
        "error": error,
    }

    return render(
        request,
        "locations/route_search.html",
        PageProcessor().decorate(context, request),
    )
