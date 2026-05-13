import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.core.page import PageProcessor
from toto.socialhub.models import Person
from toto.kanban.models import Campaign, Mission, Task
from toto.events.models import Event
from django.contrib.gis.geos import LineString, MultiLineString
from .models import (
    Address,
    MapLayer,
    Route,
    RouteChain,
    Territory,
    Zone,
)
from .forms import AddressCreateForm
from .geocode import reverse_geocode_address, forward_geocode_locations


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


# ---------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------

def geometry_json(geometry):
    return json.loads(geometry.geojson) if geometry else None


def address_payload(address):
    return {
        "id": address.pk,
        "name": str(address),
        "country_name": address.country_name,
        "state_or_province_name": address.state_or_province_name,
        "locality_name": address.locality_name,
        "street": address.street,
        "building": address.building,
        "apartment": address.apartment,
        "geometry": geometry_json(address.geometry),
    }


def zone_payload(zone):
    return {
        "id": zone.pk,
        "name": zone.name,
        "territory": {
            "id": zone.territory.pk,
            "name": zone.territory.name,
        } if zone.territory else None,
        "geometry": geometry_json(zone.geometry),
    }


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
        "route_chain": {
            "id": route.route_chain.pk,
            "name": route.route_chain.name,
        } if route.route_chain else None,
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
        "score": travel.score,
        "reviewed_at": travel.reviewed_at.isoformat() if travel.reviewed_at else None,
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


# ---------------------------------------------------------------------
# User / review helpers
# ---------------------------------------------------------------------

def current_person(request):
    if not request.user.is_authenticated:
        return None

    person = getattr(request.user, "community_profile", None)

    if person:
        return person

    return Person.objects.filter(user=request.user).first()


# ---------------------------------------------------------------------
# Main map/list
# ---------------------------------------------------------------------

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
            "detail_url": reverse("locations:zone_detail", args=[zone.pk]),
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

    for route in Route.objects.select_related(
        "route_chain",
        "start_address",
        "end_address",
    ).all():
        locations.append({
            "type": "Route",
            "name": route.name or f"Route {route.pk}",
            "detail": f"In {route.route_chain.name}" if route.route_chain else "Route",
            "geometry": geometry_json(route.geometry),
            "geometry_json": geometry_json(route.geometry),
            "detail_url": reverse("locations:route_detail", args=[route.pk]),
            "review_url": reverse("locations:route_review", args=[route.pk]),
        })

    for address in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(address),
            "detail": address.locality_name,
            "geometry": geometry_json(address.geometry),
            "geometry_json": geometry_json(address.geometry),
            "detail_url": reverse("locations:address_detail", args=[address.pk]),
            "review_url": reverse("travels:visit_review", args=[address.pk]),
        })

    from toto.locations.plugins.sidebar_plugins import LocationSidebarPlugin

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

    context["sidebar_plugin_sections"] = LocationSidebarPlugin.render_all(
        request=request,
        base_context=context,
    )

    return render(
        request,
        "locations/locations.html",
        PageProcessor().decorate(context, request),
    )


# ---------------------------------------------------------------------
# Detail views
# ---------------------------------------------------------------------

@login_required
def address_detail(request, pk):
    get_object_or_404(Address, pk=pk)
    return redirect("travels:visit_review", address_id=pk)


@login_required
def zone_detail(request, pk):
    zone = get_object_or_404(
        Zone.objects.select_related("territory"),
        pk=pk,
    )

    campaigns = (
        Campaign.objects
        .filter(zone=zone)
        .select_related("project", "owner")
        .prefetch_related("missions")
        .order_by("project__name", "name")
    )

    missions = (
        Mission.objects
        .filter(campaign__zone=zone)
        .select_related(
            "campaign",
            "campaign__project",
            "owner",
            "location",
            "route",
        )
        .prefetch_related("tasks")
        .order_by("campaign__project__name", "campaign__name", "title")
    )

    tasks = (
        Task.objects
        .filter(mission__campaign__zone=zone)
        .select_related(
            "mission",
            "mission__campaign",
            "mission__campaign__project",
            "column",
            "sprint",
            "assignee",
        )
        .order_by("mission__campaign__name", "mission__title", "position", "title")
    )

    addresses = (
        Address.objects
        .filter(missions__campaign__zone=zone)
        .distinct()
        .order_by("country_name", "locality_name", "street", "building")
    )

    routes = (
        Route.objects
        .filter(missions__campaign__zone=zone)
        .select_related("route_chain", "start_address", "end_address")
        .distinct()
        .order_by("route_chain__name", "sequence", "name")
    )

    events = (
        Event.objects
        .filter(zone=zone)
        .select_related("organizer", "category", "address", "route")
        .order_by("-start_time")
    )

    context = {
        "zone": zone,
        "zone_payload_json": json.dumps(zone_payload(zone)),

        "campaigns": campaigns,
        "missions": missions,
        "tasks": tasks,
        "addresses": addresses,
        "routes": routes,
        "events": events,
    }

    return render(
        request,
        "locations/zone_detail.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def route_detail(request, pk):
    route = get_object_or_404(
        Route.objects.select_related(
            "route_chain",
            "start_address",
            "end_address",
        ),
        pk=pk,
    )

    events = (
        Event.objects
        .filter(route=route)
        .select_related("organizer", "category", "address", "zone")
        .order_by("-start_time")
    )

    context = {
        "route": route,
        "route_payload_json": json.dumps(route_payload(route)),
        "events": events,
    }

    return render(
        request,
        "locations/route_detail.html",
        PageProcessor().decorate(context, request),
    )

# ---------------------------------------------------------------------
# Route search
# ---------------------------------------------------------------------

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


# ---------------------------------------------------------------------
# Compatibility review URL
# ---------------------------------------------------------------------

@login_required
def route_review(request, pk):
    return route_detail(request, pk)


# ---------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------

@login_required
def address_create(request):
    initial_latitude = request.GET.get("lat")
    initial_longitude = request.GET.get("lng")

    if request.method == "POST":
        form = AddressCreateForm(request.POST)

        if form.is_valid():
            address = form.save()
            messages.success(request, "Address saved.")
            return redirect("locations:address_detail", pk=address.pk)

    else:
        geocoded_initial = reverse_geocode_address(
            initial_latitude,
            initial_longitude,
        )

        query_initial = {
            "country_name": request.GET.get("country", ""),
            "state_or_province_name": request.GET.get("region", ""),
            "locality_name": request.GET.get("locality", ""),
            "street": request.GET.get("street", ""),
            "building": request.GET.get("building", ""),
        }

        initial = {
            **geocoded_initial,
            **{
                key: value
                for key, value in query_initial.items()
                if value not in (None, "")
            },
        }

        form = AddressCreateForm(
            latitude=initial_latitude,
            longitude=initial_longitude,
            initial=initial,
        )

    context = {
        "form": form,
        "latitude": initial_latitude or "",
        "longitude": initial_longitude or "",
    }

    return render(
        request,
        "locations/address_form.html",
        PageProcessor().decorate(context, request),
    )


@require_POST
@login_required
def route_save(request):
    name = request.POST.get("name", "").strip()
    route_json = request.POST.get("route_json", "").strip()

    start_address_id = request.POST.get("start_address")
    end_address_id = request.POST.get("end_address")

    if not name:
        messages.error(request, "Route name is required.")
        return redirect("locations:route_search")

    if not route_json:
        messages.error(request, "No route geometry was provided.")
        return redirect("locations:route_search")

    try:
        payload = json.loads(route_json)
    except json.JSONDecodeError:
        messages.error(request, "Route geometry is invalid.")
        return redirect("locations:route_search")

    # Accept either:
    # 1. {"type": "Feature", "geometry": {...}, "properties": {...}}
    # 2. {"type": "LineString", "coordinates": [...]}
    # 3. {"type": "MultiLineString", "coordinates": [...]}
    geometry_payload = payload.get("geometry") if payload.get("type") == "Feature" else payload

    if not geometry_payload:
        messages.error(request, "Route geometry is missing.")
        return redirect("locations:route_search")

    try:
        geometry = GEOSGeometry(json.dumps(geometry_payload), srid=4326)
    except (TypeError, ValueError):
        messages.error(request, "Route coordinates could not be converted.")
        return redirect("locations:route_search")

    if geometry.geom_type == "LineString":
        route_geometry = MultiLineString(geometry, srid=4326)

    elif geometry.geom_type == "MultiLineString":
        route_geometry = geometry
        route_geometry.srid = 4326

    else:
        messages.error(
            request,
            "Only LineString and MultiLineString routes can be saved.",
        )
        return redirect("locations:route_search")

    start_address = None
    end_address = None

    if start_address_id and not str(start_address_id).startswith("temporary:"):
        start_address = Address.objects.filter(pk=start_address_id).first()

    if end_address_id and not str(end_address_id).startswith("temporary:"):
        end_address = Address.objects.filter(pk=end_address_id).first()

    route = Route.objects.create(
        name=name,
        geometry=route_geometry,
        start_address=start_address,
        end_address=end_address,
    )

    messages.success(request, f"Route '{route.name}' saved.")
    return redirect("locations:route_detail", pk=route.pk)


@login_required
def location_search_api(request):
    query = request.GET.get("q", "").strip()

    if len(query) < 2:
        return JsonResponse({"results": []})

    return JsonResponse({
        "results": forward_geocode_locations(query),
    })