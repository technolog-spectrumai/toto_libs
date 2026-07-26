import json

import yaml
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.people.models import Person
from toto.events.models import ScheduledEvent
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


# URL-slug -> model for the generic detail page + metadata editor.
DETAIL_MODELS = {
    "address": Address,
    "territory": Territory,
    "zone": Zone,
    "routechain": RouteChain,
    "route": Route,
}

# Location kinds that carry a free-text note, mapped to their field name.
NOTE_FIELDS = {
    "address": "note",
    "route": "notes",
}


def location_detail_url(kind, pk):
    return reverse("locations:location_detail", args=[kind, pk])


def _parse_metadata(text, fmt):
    """Parse metadata text as JSON or YAML. Empty -> {}.

    YAML is a superset of JSON, but we honour the declared format so the editor's
    current mode produces precise, matching error messages.
    """
    text = (text or "").strip()
    if not text:
        return {}
    if fmt == "yaml":
        return yaml.safe_load(text)
    return json.loads(text)


def _detail_fields(kind, obj):
    """Human-readable (label, value) rows shown on the detail page per type."""
    if kind == "address":
        return [
            ("Country", obj.country_name),
            ("State / Province", obj.state_or_province_name),
            ("Locality", obj.locality_name),
            ("Street", obj.street),
            ("Building", obj.building),
            ("Apartment", obj.apartment),
        ]
    if kind == "territory":
        return [
            ("Name", obj.name),
            ("Capital", str(obj.capital) if obj.capital else None),
        ]
    if kind == "zone":
        return [
            ("Name", obj.name),
            ("Territory", obj.territory.name if obj.territory else None),
        ]
    if kind == "routechain":
        return [
            ("Name", obj.name),
            ("Description", obj.description),
            ("Routes", obj.routes.count()),
        ]
    if kind == "route":
        return [
            ("Name", obj.name or f"Route {obj.pk}"),
            ("Route chain", obj.route_chain.name if obj.route_chain else None),
            ("Sequence", obj.sequence),
            ("Start address", str(obj.start_address) if obj.start_address else None),
            ("End address", str(obj.end_address) if obj.end_address else None),
        ]
    return []


def _metadata_context(kind, obj):
    """Context the reusable metadata editor section needs."""
    return {
        "kind": kind,
        "metadata_json": json.dumps(obj.metadata or {}, indent=2, ensure_ascii=False),
        "metadata_save_url": reverse("locations:metadata_save", args=[kind, obj.pk]),
        "metadata_convert_url": reverse("locations:metadata_convert"),
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
        "note": address.note,
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
        "notes": route.notes,
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
        detail_url = location_detail_url("territory", territory.pk)
        locations.append({
            "type": "Territory",
            "name": territory.name or f"Territory {territory.pk}",
            "detail": f"Capital: {territory.capital}" if territory.capital else "Territory",
            "geometry": geometry_json(territory.geometry),
            "geometry_json": geometry_json(territory.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for zone in Zone.objects.select_related("territory").all():
        detail_url = location_detail_url("zone", zone.pk)
        locations.append({
            "type": "Zone",
            "name": zone.name or f"Zone {zone.pk}",
            "detail": f"Inside {zone.territory.name}" if zone.territory else "Standalone zone",
            "geometry": geometry_json(zone.geometry),
            "geometry_json": geometry_json(zone.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for chain in RouteChain.objects.prefetch_related("routes").all():
        geometry = route_chain_geometry(chain)
        detail_url = location_detail_url("routechain", chain.pk)

        locations.append({
            "type": "Route Chain",
            "name": chain.name or f"Route Chain {chain.pk}",
            "detail": chain.description or f"{chain.routes.count()} routes",
            "geometry": geometry,
            "geometry_json": geometry,
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for route in Route.objects.select_related(
        "route_chain",
        "start_address",
        "end_address",
    ).all():
        detail_url = location_detail_url("route", route.pk)
        locations.append({
            "type": "Route",
            "name": route.name or f"Route {route.pk}",
            "detail": f"In {route.route_chain.name}" if route.route_chain else "Route",
            "note": route.notes,
            "geometry": geometry_json(route.geometry),
            "geometry_json": geometry_json(route.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for address in Address.objects.all():
        detail_url = location_detail_url("address", address.pk)
        locations.append({
            "type": "Address",
            "name": str(address),
            "detail": address.locality_name,
            "note": address.note,
            "geometry": geometry_json(address.geometry),
            "geometry_json": geometry_json(address.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    from toto.locations.plugins.map_plugins import LocationMapPlugin
    locations.extend(LocationMapPlugin.get_items())

    from toto.locations.plugins.sidebar_plugins import LocationSidebarPlugin
    from toto.locations.plugins.context_plugins import LocationContextPlugin

    from toto.vault.models import VaultFile
    vault_geojson_files = list(
        VaultFile.objects
        .filter(file_type="json")
        .select_related("bucket")
        .order_by("-uploaded_at")[:300]
    )

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
        "vault_files_json": json.dumps([
            {
                "id": f.id,
                "title": f.title,
                "bucket": f.bucket.name if f.bucket else "—",
                "uploaded_at": f.uploaded_at.strftime("%Y-%m-%d %H:%M"),
                "is_encrypted": f.is_encrypted,
            }
            for f in vault_geojson_files
        ]),
        **LocationContextPlugin.get_context(),
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
    return redirect("locations:location_detail", kind="address", pk=pk)


@login_required
def zone_detail(request, pk):
    zone = get_object_or_404(
        Zone.objects.select_related("territory"),
        pk=pk,
    )

    # The campaign/mission/task sections (and the Address/Route lookups that
    # traverse the kanban Mission reverse relations) only exist when the host
    # installs toto.kanban; without it the zone page shows the zone alone.
    if apps.is_installed("toto.kanban"):
        from toto.kanban.models import Campaign, Mission, Task

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
                "assignee__person",
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
    else:
        campaigns = missions = tasks = []
        addresses = Address.objects.none()
        routes = Route.objects.none()

    context = {
        "zone": zone,
        "zone_payload_json": json.dumps(zone_payload(zone)),

        "campaigns": campaigns,
        "missions": missions,
        "tasks": tasks,
        "addresses": addresses,
        "routes": routes,
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

    from toto.locations.plugins.url_plugins import LocationUrlPlugin

    context = {
        "route": route,
        "route_payload_json": json.dumps(route_payload(route)),
        "travel_create_url": LocationUrlPlugin.get_url("travel_create"),
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
    # Route editing is a GIS-only feature (this view is only mounted on a GIS
    # host); import GEOS lazily so the module loads GDAL-free on a light host.
    from django.contrib.gis.geos import GEOSGeometry, MultiLineString

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


# ---------------------------------------------------------------------
# Layer import
# ---------------------------------------------------------------------

@login_required
@require_POST
def api_import_layer(request):
    from collections import defaultdict
    from django.contrib.gis.geos import GEOSGeometry
    from .models import MapLayerPolygon

    uploaded = request.FILES.get("file")
    vault_file_id = request.POST.get("vault_file_id", "").strip()

    if not uploaded and not vault_file_id:
        return JsonResponse({"error": "Provide a file upload or a vault_file_id."}, status=400)

    try:
        if uploaded:
            raw_bytes = uploaded.read()
            password = request.POST.get("password", "").strip()
            if password:
                try:
                    from cryptography.fernet import Fernet, InvalidToken
                    keyring = request.user.user_strongboxes.first()
                    if not keyring:
                        return JsonResponse({"error": "No encryption strongbox found for your account."}, status=400)
                    key = keyring.derive_key(password)
                    raw_bytes = Fernet(key).decrypt(raw_bytes)
                except InvalidToken:
                    return JsonResponse({"error": "Wrong password or file is not encrypted."}, status=400)
            raw = raw_bytes.decode("utf-8")
        else:
            from toto.vault.models import VaultFile
            vf = VaultFile.objects.get(pk=vault_file_id)
            if vf.is_encrypted:
                password = request.POST.get("password", "").strip()
                if not password:
                    return JsonResponse({"error": "encrypted", "message": "File is encrypted — provide a password."}, status=422)
                try:
                    content_bytes, _ = vf.get_strategy().decrypt_to_bytes(vf, password=password)
                    raw = content_bytes.decode("utf-8")
                except Exception:
                    return JsonResponse({"error": "Wrong password or corrupted file."}, status=400)
            else:
                vf.file.open("rb")
                raw = vf.file.read().decode("utf-8")
                vf.file.close()
        data = json.loads(raw)
    except VaultFile.DoesNotExist:
        return JsonResponse({"error": "Vault file not found."}, status=404)
    except Exception as exc:
        return JsonResponse({"error": f"Invalid GeoJSON — could not parse file: {exc}"}, status=400)

    if data.get("type") != "FeatureCollection":
        return JsonResponse({"error": "Expected a GeoJSON FeatureCollection."}, status=400)

    features = data.get("features") or []
    if not features:
        return JsonResponse({"error": "File contains no features."}, status=400)

    # Group features by layer_slug property
    groups: dict[str, list] = defaultdict(list)
    for feat in features:
        slug = (feat.get("properties") or {}).get("layer_slug") or "imported-layer"
        groups[slug].append(feat)

    name_override = request.POST.get("name", "").strip()

    results = []
    for layer_slug, feats in groups.items():
        first_props = feats[0].get("properties") or {}
        # If caller supplied a name and there is only one layer, use it
        layer_name = (name_override if len(groups) == 1 else "") or first_props.get("layer_name") or layer_slug
        unit = first_props.get("unit", "")

        raw_values = [
            (f.get("properties") or {}).get("value")
            for f in feats
            if (f.get("properties") or {}).get("value") is not None
        ]
        min_val = min(raw_values) if raw_values else None
        max_val = max(raw_values) if raw_values else None

        layer, _ = MapLayer.objects.update_or_create(
            slug=layer_slug,
            defaults={
                "name": layer_name,
                "unit": unit,
                "min_value": min_val,
                "max_value": max_val,
                "is_active": True,
            },
        )
        layer.polygons.all().delete()

        _skip_props = {"layer_slug", "layer_name", "id", "name", "value", "unit"}
        created = 0
        for feat in feats:
            geom_data = feat.get("geometry")
            props = feat.get("properties") or {}
            value = props.get("value")
            if geom_data is None or value is None:
                continue

            try:
                geom = GEOSGeometry(json.dumps(geom_data), srid=4326)
            except Exception:
                continue

            polys = list(geom) if geom.geom_type == "MultiPolygon" else (
                [geom] if geom.geom_type == "Polygon" else []
            )
            feat_name = props.get("name", "")
            extra = {k: v for k, v in props.items() if k not in _skip_props}

            for poly in polys:
                MapLayerPolygon.objects.create(
                    layer=layer,
                    geometry=poly,
                    center=poly.centroid,
                    value=float(value),
                    name=feat_name,
                    properties=extra,
                )
                created += 1

        results.append({"slug": layer_slug, "name": layer_name, "polygon_count": created})

    return JsonResponse({"imported": results})


# ---------------------------------------------------------------------
# Generic per-object detail page (+ JSON/YAML metadata section)
# ---------------------------------------------------------------------

@login_required
def location_detail(request, kind, pk):
    """Unified detail page for any location object.

    Shows the object's key fields, a geometry preview, and the editable
    JSON/YAML metadata section (anchored at #metadata).
    """
    model = DETAIL_MODELS.get(kind)
    if model is None:
        raise Http404(f"Unknown location kind '{kind}'.")

    obj = get_object_or_404(model, pk=pk)

    if kind == "routechain":
        geom_json = route_chain_geometry(obj)
    else:
        geom = getattr(obj, "geometry", None)
        geom_json = geometry_json(geom) if geom else None

    context = {
        "obj": obj,
        "object_label": str(obj),
        "object_type": model._meta.verbose_name.title(),
        "fields": [(label, value) for label, value in _detail_fields(kind, obj)],
        "geometry_json": json.dumps(geom_json),
        "has_geometry": geom_json is not None,
        "back_url": reverse("locations:locations_all"),
        "note_field": NOTE_FIELDS.get(kind),
        "note_value": getattr(obj, NOTE_FIELDS[kind], "") if kind in NOTE_FIELDS else "",
        "note_save_url": reverse("locations:note_save", args=[kind, pk]) if kind in NOTE_FIELDS else "",
        **_metadata_context(kind, obj),
    }

    return render(
        request,
        "locations/location_detail.html",
        PageProcessor().decorate(context, request),
    )


@login_required
@require_POST
def metadata_save(request, kind, pk):
    """Persist a location object's metadata from JSON or YAML text (AJAX)."""
    model = DETAIL_MODELS.get(kind)
    if model is None:
        raise Http404(f"Unknown location kind '{kind}'.")

    obj = get_object_or_404(model, pk=pk)
    fmt = request.POST.get("format", "json")

    try:
        parsed = _parse_metadata(request.POST.get("metadata"), fmt)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return JsonResponse(
            {"status": "error", "error": f"Invalid {fmt.upper()}: {exc}"}, status=400
        )

    if parsed is None:
        parsed = {}

    if not isinstance(parsed, dict):
        return JsonResponse(
            {"status": "error", "error": "Metadata must be a mapping/object."},
            status=400,
        )

    obj.metadata = parsed
    obj.save(update_fields=["metadata"])
    return JsonResponse({"status": "ok"})


@login_required
@require_POST
def note_save(request, kind, pk):
    """Persist a location object's free-text note (plain form POST + redirect)."""
    field = NOTE_FIELDS.get(kind)
    model = DETAIL_MODELS.get(kind)
    if field is None or model is None:
        raise Http404(f"No note field for kind '{kind}'.")

    obj = get_object_or_404(model, pk=pk)
    setattr(obj, field, request.POST.get("note", "").strip())
    obj.save(update_fields=[field])
    messages.success(request, "Note saved.")

    return redirect(
        request.POST.get("next")
        or reverse("locations:location_detail", args=[kind, pk])
    )


@login_required
@require_POST
def metadata_convert(request):
    """Convert metadata text between JSON and YAML (object-agnostic, AJAX)."""
    text = request.POST.get("text", "")
    src = request.POST.get("from", "json")
    dst = request.POST.get("to", "json")

    try:
        data = _parse_metadata(text, src)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        return JsonResponse(
            {"status": "error", "error": f"Invalid {src.upper()}: {exc}"}, status=400
        )

    if data is None:
        data = {}

    if not isinstance(data, dict):
        return JsonResponse(
            {"status": "error", "error": "Metadata must be a mapping/object."},
            status=400,
        )

    if dst == "yaml":
        text_out = yaml.safe_dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
    else:
        text_out = json.dumps(data, indent=2, ensure_ascii=False)

    return JsonResponse({"status": "ok", "text": text_out})