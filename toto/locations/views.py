import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.core.page import PageProcessor
from toto.socialhub.models import Person

from .models import (
    Address,
    MapLayer,
    Route,
    RouteChain,
    Territory,
    Travel,
    Visit,
    Zone,
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


def route_payload(route):
    return {
        "id": route.pk,
        "name": route.name or f"Route {route.pk}",
        "geometry": geometry_json(route.geometry),
        "start_address": {
            "id": route.start_address.pk,
            "label": str(route.start_address),
            "geometry": geometry_json(route.start_address.geometry),
        } if route.start_address else None,
        "end_address": {
            "id": route.end_address.pk,
            "label": str(route.end_address),
            "geometry": geometry_json(route.end_address.geometry),
        } if route.end_address else None,
    }


def travel_payload(travel):
    route = travel.route

    return {
        "id": travel.pk,
        "info": travel.info,
        "starts_at": travel.starts_at.isoformat() if travel.starts_at else None,
        "ends_at": travel.ends_at.isoformat() if travel.ends_at else None,
        "route": route_payload(route) if route else None,
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


def current_person(request):
    if not request.user.is_authenticated:
        return None

    person = getattr(request.user, "community_profile", None)
    if person:
        return person

    return Person.objects.filter(user=request.user).first()


def location_reviews_queryset(addresses):
    address_ids = [address.pk for address in addresses if address]

    return (
        Visit.objects
        .filter(location_id__in=address_ids)
        .select_related("participant", "location")
        .order_by("-id")
    )


@login_required
def locations_all(request):
    locations = []

    for territory in Territory.objects.select_related("capital").all():
        locations.append({
            "type": "Territory",
            "name": territory.name or f"Territory {territory.pk}",
            "detail": f"Capital: {territory.capital}" if territory.capital else "Territory",
            "geometry": geometry_json(territory.geometry),
            "geometry_json": geometry_json(territory.geometry),
        })

    for zone in Zone.objects.select_related("territory").all():
        locations.append({
            "type": "Zone",
            "name": zone.name or f"Zone {zone.pk}",
            "detail": f"Inside {zone.territory.name}" if zone.territory else "Standalone zone",
            "geometry": geometry_json(zone.geometry),
            "geometry_json": geometry_json(zone.geometry),
        })

    for chain in RouteChain.objects.prefetch_related("routes").all():
        geometry = route_chain_geometry(chain)
        locations.append({
            "type": "Route Chain",
            "name": chain.name or f"Route Chain {chain.pk}",
            "detail": chain.description or f"{chain.routes.count()} routes",
            "geometry": geometry,
            "geometry_json": geometry,
        })

    for route in Route.objects.select_related("route_chain").all():
        locations.append({
            "type": "Route",
            "name": route.name or f"Route {route.pk}",
            "detail": f"In {route.route_chain.name}" if route.route_chain else "Route",
            "geometry": geometry_json(route.geometry),
            "geometry_json": geometry_json(route.geometry),
            "review_url": reverse("locations:route_review", args=[route.pk]),
        })

    for address in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(address),
            "detail": address.locality_name,
            "geometry": geometry_json(address.geometry),
            "geometry_json": geometry_json(address.geometry),
            "review_url": reverse("locations:visit_review", args=[address.pk]),
        })

    context = {
        "locations": locations,
        "locations_json": json.dumps(locations),
        "map_layers_json": json.dumps([
            map_layer_payload(layer)
            for layer in MapLayer.objects
            .filter(is_active=True)
            .prefetch_related("polygons")
            .order_by("name")
        ]),
    }

    return render(
        request,
        "locations/locations.html",
        PageProcessor().decorate(context, request),
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

    if str(address_id).startswith("temporary:"):
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

            route = fetch_traversable_route(
                start_lng,
                start_lat,
                end_lng,
                end_lat,
                form["mode"],
            )
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


@login_required
def route_review(request, pk):
    route = get_object_or_404(
        Route.objects.select_related(
            "route_chain",
            "start_address",
            "end_address",
        ),
        pk=pk,
    )

    review_locations = [
        location
        for location in (route.start_address, route.end_address)
        if location
    ]

    context = {
        "route": route,
        "route_payload_json": json.dumps(route_payload(route)),
        "travels": (
            Travel.objects
            .filter(route=route)
            .prefetch_related("participants")
            .order_by("-starts_at")
        ),
        "review_locations": review_locations,
        "reviews": location_reviews_queryset(review_locations),
        "person": current_person(request),
    }

    return render(
        request,
        "locations/route_review.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def travel_review(request, pk):
    travel = get_object_or_404(
        Travel.objects
        .select_related(
            "route",
            "route__start_address",
            "route__end_address",
            "route__route_chain",
        )
        .prefetch_related("participants"),
        pk=pk,
    )

    route = travel.route
    review_locations = []

    if route:
        review_locations = [
            location
            for location in (route.start_address, route.end_address)
            if location
        ]

    context = {
        "travel": travel,
        "route": route,
        "travel_payload_json": json.dumps(travel_payload(travel)),
        "review_locations": review_locations,
        "reviews": location_reviews_queryset(review_locations),
        "person": current_person(request),
    }

    return render(
        request,
        "locations/travel_review.html",
        PageProcessor().decorate(context, request),
    )


@require_POST
@login_required
def submit_visit_review(request, address_id):
    person = current_person(request)
    fallback_url = reverse("locations:locations_all")
    next_url = request.POST.get("next") or fallback_url

    if not person:
        messages.error(
            request,
            "You need a community profile before you can submit a visit review.",
        )
        return redirect(next_url)

    location = get_object_or_404(Address, pk=address_id)

    review = request.POST.get("review", "").strip()
    raw_score = request.POST.get("score")

    score = None
    if raw_score not in (None, ""):
        try:
            score = int(raw_score)
        except ValueError:
            messages.error(request, "Score must be a number from 1 to 5.")
            return redirect(next_url)

        if score < 1 or score > 5:
            messages.error(request, "Score must be from 1 to 5.")
            return redirect(next_url)

    Visit.objects.update_or_create(
        participant=person,
        location=location,
        defaults={
            "review": review,
            "score": score,
        },
    )

    messages.success(request, "Visit review saved.")
    return redirect(next_url)

@login_required
def visit_review(request, address_id):
    location = get_object_or_404(Address, pk=address_id)

    context = {
        "location": location,
        "location_payload_json": json.dumps({
            "id": location.pk,
            "name": str(location),
            "detail": location.locality_name,
            "geometry": geometry_json(location.geometry),
        }),
        "reviews": (
            Visit.objects
            .filter(location=location)
            .select_related("participant", "location")
            .order_by("-id")
        ),
        "person": current_person(request),
    }

    return render(
        request,
        "locations/visit_review.html",
        PageProcessor().decorate(context, request),
    )

@require_POST
@login_required
def update_travel_info(request, pk):
    travel = get_object_or_404(Travel, pk=pk)

    travel.info = request.POST.get("info", "").strip()
    travel.save(update_fields=["info"])

    messages.success(request, "Travel info saved.")
    return redirect("locations:travel_review", pk=travel.pk)