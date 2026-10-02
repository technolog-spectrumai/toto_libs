import json

import yaml
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from toto.core.safe_next import safe_next
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
from . import geocoding
from .forms import AddressCreateForm
from .geocode import geocoding_enabled, geocoding_headers, geocoding_settings


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
    "maplayer": MapLayer,
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


def _detail_fields(kind, obj, user=None):
    """Human-readable (label, value) rows shown on the detail page per type.
    What the row points at (a capital, a territory, a route's ends) is named
    only when ``user`` may read it (map domains, `access`)."""
    from .access import readable_or_none, readable_routes

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
            ("Capital", _label(readable_or_none(user, obj.capital))),
        ]
    if kind == "zone":
        return [
            ("Name", obj.name),
            ("Territory", _name(readable_or_none(user, obj.territory))),
        ]
    if kind == "routechain":
        return [
            ("Name", obj.name),
            ("Description", obj.description),
            ("Routes", readable_routes(user, obj.routes.all()).count()),
        ]
    if kind == "maplayer":
        return [
            ("Unit", obj.unit or None),
            ("Active", "yes" if obj.is_active else "no"),
            ("Owner", obj.owner.display_name if obj.owner else None),
            ("Polygons", obj.polygons.count()),
        ]
    if kind == "route":
        return [
            ("Name", obj.name or f"Route {obj.pk}"),
            ("Route chain", obj.route_chain.name if obj.route_chain else None),
            ("Sequence", obj.sequence),
            ("Start address", _label(readable_or_none(user, obj.start_address))),
            ("End address", _label(readable_or_none(user, obj.end_address))),
        ]
    return []


def _label(obj):
    return str(obj) if obj is not None else None


def _name(obj):
    return obj.name if obj is not None else None


def _may_write(user, obj):
    from .access import may_write

    return may_write(user, obj)


def _readable_or_404(request, model, pk):
    """The object — or the 404 a missing one gets when its map domains hide it
    from this reader (`access.may_read`; a route chain is never hidden)."""
    from .access import may_read

    obj = get_object_or_404(model, pk=pk)
    if not may_read(request.user, obj):
        raise Http404("No such location.")
    return obj


def _metadata_context(kind, obj):
    """Context the reusable metadata editor section needs; a kind with no
    metadata column (a map layer) gets none and the page shows no editor."""
    if not hasattr(obj, "metadata"):
        return {"kind": kind, "has_metadata": False}
    return {
        "kind": kind,
        "has_metadata": True,
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


def zone_payload(zone, user=None):
    from .access import readable_or_none

    territory = readable_or_none(user, zone.territory)
    return {
        "id": zone.pk,
        "name": zone.name,
        "territory": {
            "id": territory.pk,
            "name": territory.name,
        } if territory else None,
        "geometry": geometry_json(zone.geometry),
    }


def _chain_geometry(routes):
    """One MultiLineString of ``routes``, in the order given; None for none."""
    coordinates = []

    for route in routes:
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


def route_chain_geometry(route_chain, user=None):
    """The chain drawn from the routes ``user`` may read (map domains; None:
    the open ones) — a hidden route is never drawn through its chain."""
    from .access import readable_routes

    return _chain_geometry(readable_routes(user, route_chain.routes.all())
                           .order_by("sequence", "name", "pk"))


def route_payload(route, user=None):
    """A route as JSON; an end its reader may not read is left out."""
    from .access import readable_or_none

    start = readable_or_none(user, route.start_address)
    end = readable_or_none(user, route.end_address)
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
            "id": start.pk,
            "label": str(start),
            "geometry": geometry_json(start.geometry),
        } if start else None,
        "end_address": {
            "id": end.pk,
            "label": str(end),
            "geometry": geometry_json(end.geometry),
        } if end else None,
    }


def route_rows(user, routes):
    """``[{"route", "start", "end"}]``: each route with the ends ``user`` may
    read (``readable_or_none``) — what a page prints beside a route, by the
    rule ``route_payload`` keeps for its JSON (2026-10-02, crown 41: the
    pages printed both ends from the relation, a hidden home's street and a
    kept domain's address included)."""
    from .access import readable_or_none

    return [{"route": route,
             "start": readable_or_none(user, route.start_address),
             "end": readable_or_none(user, route.end_address)} for route in routes]


def travel_payload(travel, user=None):
    from .access import readable_or_none

    route = readable_or_none(user, travel.route)

    return {
        "id": travel.pk,
        "info": travel.info,
        "score": travel.score,
        "reviewed_at": travel.reviewed_at.isoformat() if travel.reviewed_at else None,
        "starts_at": travel.starts_at.isoformat() if travel.starts_at else None,
        "ends_at": travel.ends_at.isoformat() if travel.ends_at else None,
        "route": route_payload(route, user) if route else None,
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
    from .access import (
        readable_addresses, readable_layers, readable_routes, readable_territories, readable_zones,
    )

    user = request.user
    locations = []
    # What this reader may read (map domains): a hidden item is missing from
    # the map, and so is its name where another row would mention it.
    addresses = list(readable_addresses(user))
    address_ids = {address.pk for address in addresses}
    territories = list(readable_territories(user).select_related("capital"))
    territory_names = {territory.pk: territory.name for territory in territories}
    routes = list(readable_routes(user).select_related("route_chain"))

    for territory in territories:
        detail_url = location_detail_url("territory", territory.pk)
        capital = territory.capital if territory.capital_id in address_ids else None
        locations.append({
            "type": "Territory",
            "name": territory.name or f"Territory {territory.pk}",
            "detail": f"Capital: {capital}" if capital else "Territory",
            "geometry": geometry_json(territory.geometry),
            "geometry_json": geometry_json(territory.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for zone in readable_zones(user):
        detail_url = location_detail_url("zone", zone.pk)
        inside = territory_names.get(zone.territory_id)
        locations.append({
            "type": "Zone",
            "name": zone.name or f"Zone {zone.pk}",
            "detail": f"Inside {inside}" if inside else "Standalone zone",
            "geometry": geometry_json(zone.geometry),
            "geometry_json": geometry_json(zone.geometry),
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    # A chain is drawn and counted from the routes this reader may read.
    chain_routes = {}
    for route in sorted(routes, key=lambda r: (r.sequence, r.name, r.pk)):
        if route.route_chain_id:
            chain_routes.setdefault(route.route_chain_id, []).append(route)

    for chain in RouteChain.objects.all():
        geometry = _chain_geometry(chain_routes.get(chain.pk, []))
        detail_url = location_detail_url("routechain", chain.pk)

        locations.append({
            "type": "Route Chain",
            "name": chain.name or f"Route Chain {chain.pk}",
            "detail": chain.description or f"{len(chain_routes.get(chain.pk, []))} routes",
            "geometry": geometry,
            "geometry_json": geometry,
            "detail_url": detail_url,
            "metadata_url": f"{detail_url}#metadata",
        })

    for route in routes:
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

    for address in addresses:
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
    locations.extend(LocationMapPlugin.get_items(request))

    from toto.locations.plugins.sidebar_plugins import LocationSidebarPlugin
    from toto.locations.plugins.context_plugins import LocationContextPlugin

    # The GeoJSON files the viewer may read — never every json file on the
    # platform (until 2026-09-25 this listed every member's file titles).
    from toto.vault.filetree import accessible_files
    vault_geojson_files = list(
        accessible_files(request.user, file_types=("json",))
        .select_related("bucket")
        .order_by("-uploaded_at")[:300]
    )
    map_layers = [
        map_layer_payload(layer)
        for layer in readable_layers(request.user, MapLayer.objects.filter(is_active=True))
        .prefetch_related("polygons")
        .order_by("name")
    ]
    vault_files = [
        {
            "id": f.id,
            "title": f.title,
            "bucket": f.bucket.name if f.bucket else "—",
            "uploaded_at": f.uploaded_at.strftime("%Y-%m-%d %H:%M"),
            "is_encrypted": f.is_encrypted,
        }
        for f in vault_geojson_files
    ]

    # The page hands each list to its scripts as a json_script block
    # (2026-10-02, crown 41); the *_json strings stay for whoever reads them.
    context = {
        "locations": locations,
        "locations_json": json.dumps(locations),
        "map_layers": map_layers,
        "map_layers_json": json.dumps(map_layers),
        "vault_files": vault_files,
        "vault_files_json": json.dumps(vault_files),
        # The map's web search is a charged place lookup; a host that makes
        # no outbound calls renders none, and Enter keeps meaning "the first
        # match on this map".
        "geocoding_enabled": geocoding_enabled(geocoding_settings()),
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
    _readable_or_404(request, Address, pk)
    return redirect("locations:location_detail", kind="address", pk=pk)


@login_required
def zone_detail(request, pk):
    from .access import readable_addresses, readable_or_none, readable_routes

    zone = _readable_or_404(request, Zone.objects.select_related("territory"), pk)

    # The campaign/mission/task sections (and the Address/Route lookups that
    # traverse the kanban Mission reverse relations) only exist when the host
    # installs toto.kanban; without it the zone page shows the zone alone.
    if apps.is_installed("toto.kanban"):
        from django.db.models import Q as _Q
        from toto.kanban.models import Campaign, Mission, visible_missions_for

        campaigns = (
            Campaign.objects
            .filter(zone=zone)
            .select_related("project", "owner")
            .prefetch_related("missions")
            .order_by("project__name", "name")
        )

        # Effective-zone semantics: a mission is "in" this zone when it points
        # at it directly, or inherits it from the campaign without overriding.
        # And only the missions this user may see.
        missions = (
            visible_missions_for(
                request.user,
                Mission.objects.filter(
                    _Q(zone=zone) | _Q(zone__isnull=True, campaign__zone=zone)
                ),
            )
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

        addresses = (
            readable_addresses(request.user)
            .filter(missions__in=missions)
            .distinct()
            .order_by("country_name", "locality_name", "street", "building")
        )

        routes = (
            readable_routes(request.user)
            .filter(missions__in=missions)
            .select_related("route_chain", "start_address", "end_address")
            .distinct()
            .order_by("route_chain__name", "sequence", "name")
        )
    else:
        campaigns = missions = []
        addresses = Address.objects.none()
        routes = Route.objects.none()

    payload = zone_payload(zone, request.user)
    context = {
        "zone": zone,
        # What the page prints, by the rule its JSON keeps (2026-10-02,
        # crown 41): the territory and each route's ends a reader may read.
        "zone_territory": readable_or_none(request.user, zone.territory),
        "zone_payload": payload,
        "zone_payload_json": json.dumps(payload),

        "campaigns": campaigns,
        "missions": missions,
        "addresses": addresses,
        "routes": routes,
        "route_rows": route_rows(request.user, routes),
    }

    return render(
        request,
        "locations/zone_detail.html",
        PageProcessor().decorate(context, request),
    )


@login_required
def route_detail(request, pk):
    route = _readable_or_404(request, Route.objects.select_related(
        "route_chain", "start_address", "end_address"), pk)

    from toto.locations.plugins.url_plugins import LocationUrlPlugin

    payload = route_payload(route, request.user)
    row, = route_rows(request.user, [route])
    context = {
        "route": route,
        # The ends the page prints, by the rule its JSON keeps (2026-10-02,
        # crown 41): it printed both from the relation, so a hidden home's
        # street, with a link to it, reached every reader of the route.
        "route_start": row["start"],
        "route_end": row["end"],
        "route_payload": payload,
        "route_payload_json": json.dumps(payload),
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
    # The routing service is OpenStreetMap's too, and its usage policy asks
    # for the same identifying User-Agent the geocoder sends; urllib's default
    # is the anonymous traffic such services throttle first.
    request = Request(url, headers=geocoding_headers(geocoding_settings()))

    try:
        with urlopen(request, timeout=12) as response:
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


def selected_address_coordinates(address_id, label, user=None):
    """The (lng, lat) of a saved address ``user`` may read (map domains; None:
    an open one); a hidden one is "not available", like a missing one."""
    from .access import readable_addresses

    if not address_id:
        return None

    if str(address_id).startswith("temporary:"):
        return None

    try:
        address = readable_addresses(user).get(pk=address_id, geometry__isnull=False)
    except (Address.DoesNotExist, ValueError):
        raise ValueError(f"{label} address is not available.")

    return address_coordinates(address)


# ---------------------------------------------------------------------
# Typed places (2026-09-28): a route from "Gdansk" to "Warsaw". Each name is
# one charged place lookup (toto.locations.geocoding), so the page resolves
# names only on a POST (CSRF, never a link) and then redirects to the GET
# that draws the route. That URL carries the answer — coordinates, the
# provider's label, and `<side>_resolved`, the text they answer — so a reload,
# a new mode or a re-submit with the same text asks nobody and costs nothing.
# ---------------------------------------------------------------------

ROUTE_ENDS = (("start", "Start"), ("end", "End"))


def typed_place(value):
    """A typed place name with its whitespace folded; "" for none."""
    return " ".join(str(value or "").split())


def lookup_typed_places(user, names):
    """Yield (side, name, match or None) for each typed name in ``names``.

    ``names`` is [(side, text)]; an empty text is skipped. The whole batch is
    checked — every name's length, then the quota and the purse — before the
    first lookup, so the second name cannot be refused for bad input or money
    after the first was charged. A generator, so a caller keeps what resolved
    before a refusal (throttle, provider down) stopped the rest: those lookups
    are paid for.
    """
    pending = [(side, name) for side, name in names if name]
    if not pending:
        return
    for _side, name in pending:
        geocoding.clean_query(name)
    geocoding.check_affordable(user, len(pending))
    for side, name in pending:
        yield side, name, geocoding.first_match(user, name)


def no_place_found(name):
    return _("No place found for '%(name)s'.") % {"name": name}


def _route_form(params, default_start, default_end):
    form = {
        "mode": params.get("mode", "car"),
        "start_address": params.get("start_address", str(default_start.pk) if default_start else ""),
        "end_address": params.get("end_address", str(default_end.pk) if default_end else ""),
        "start_lat": params.get("start_lat", str(default_start.geometry.y) if default_start else "54.3487"),
        "start_lng": params.get("start_lng", str(default_start.geometry.x) if default_start else "18.6538"),
        "end_lat": params.get("end_lat", str(default_end.geometry.y) if default_end else "54.4067"),
        "end_lng": params.get("end_lng", str(default_end.geometry.x) if default_end else "18.6717"),
    }

    for side, _label in ROUTE_ENDS:
        query = typed_place(params.get(f"{side}_query"))
        form[f"{side}_query"] = query
        # Markers for a cleared box are dropped, not carried: they describe
        # a name nobody is asking for any more.
        form[f"{side}_resolved"] = typed_place(params.get(f"{side}_resolved")) if query else ""
        form[f"{side}_label"] = str(params.get(f"{side}_label") or "").strip() if query else ""

    return form


def _is_resolved(form, side):
    query = form[f"{side}_query"]
    return bool(query) and query == form[f"{side}_resolved"]


def _route_end(form, side, label, user=None):
    """(lng, lat) for one end: a typed place (resolved already), else the
    saved address, else the typed coordinates. The typed name wins over the
    address select, which always has a default."""
    if form[f"{side}_query"]:
        if not _is_resolved(form, side):
            # Only a POST asks the provider; a GET naming a place (an old
            # link, a hand-edited URL) must not spend anybody's mana.
            raise ValueError(_("'%(name)s' has not been looked up yet: press Search route.")
                             % {"name": form[f"{side}_query"]})
    else:
        selected = selected_address_coordinates(form[f"{side}_address"], label, user)
        if selected:
            lng, lat = selected
            form[f"{side}_lng"] = str(lng)
            form[f"{side}_lat"] = str(lat)
            return lng, lat

    lat = parse_coordinate(form[f"{side}_lat"], f"{label} latitude", -90, 90)
    lng = parse_coordinate(form[f"{side}_lng"], f"{label} longitude", -180, 180)
    return lng, lat


def _resolve_route_places(user, form):
    """Look up the typed names that are not resolved yet, writing each answer
    into ``form`` as it comes, so the page shows what was paid for even when a
    later name fails. Raises ValueError naming every place nothing was found
    for, or a billing/geocoding refusal."""
    if form["mode"] not in {mode["value"] for mode in ROUTING_MODE_OPTIONS}:
        raise ValueError("Mode must be car, bicycle, foot, or public transport.")

    # The free end first: a route with a broken end must not be refused after
    # the other end's lookup was charged.
    for side, label in ROUTE_ENDS:
        if not form[f"{side}_query"]:
            _route_end(form, side, label, user)

    names = [(side, form[f"{side}_query"]) for side, _label in ROUTE_ENDS
             if form[f"{side}_query"] and not _is_resolved(form, side)]
    missing = []

    for side, name, match in lookup_typed_places(user, names):
        if match is None:
            missing.append(no_place_found(name))
            continue
        form[f"{side}_lat"] = str(match["lat"])
        form[f"{side}_lng"] = str(match["lng"])
        form[f"{side}_label"] = match.get("label") or match.get("name") or name
        form[f"{side}_resolved"] = name
        form[f"{side}_address"] = ""

    if missing:
        raise ValueError(" ".join(missing))


def _route_end_name(form, side, address_labels, private=frozenset()):
    """What the default route name calls one end — and the map's marker,
    which passes no ``private``. A saved address in ``private`` (somebody's
    home, ``access.home_pin_ids``) is "A private address" in the name
    (2026-10-02, crown 41): the name is saved with the route and read by
    everyone who reads the route, after its person stops sharing too."""
    if _is_resolved(form, side):
        return form[f"{side}_query"]
    if not form[f"{side}_query"] and form[f"{side}_address"] in address_labels:
        if form[f"{side}_address"] in private:
            return _("A private address")
        return address_labels[form[f"{side}_address"]]
    return f"{form[f'{side}_lat']}, {form[f'{side}_lng']}"


@login_required
def route_search(request):
    from .access import home_pin_ids, readable_addresses

    addresses = list(
        readable_addresses(request.user)
        .filter(geometry__isnull=False)
        .order_by("country_name", "locality_name", "street", "building")
    )

    default_start = addresses[0] if addresses else None
    default_end = addresses[1] if len(addresses) > 1 else default_start

    params = request.POST if request.method == "POST" else request.GET
    form = _route_form(params, default_start, default_end)

    route = None
    error = ""

    if request.method == "POST":
        try:
            _resolve_route_places(request.user, form)
        except (ValueError, *geocoding.REFUSALS) as exc:
            # Shown in the page's error box: "No place found for ...", the
            # mana sentence, a throttle. What did resolve stays in the form.
            error = str(exc)
        else:
            return redirect(f"{reverse('locations:route_search')}?{urlencode(form)}")

    elif request.GET:
        try:
            if form["mode"] not in {mode["value"] for mode in ROUTING_MODE_OPTIONS}:
                raise ValueError("Mode must be car, bicycle, foot, or public transport.")

            start_lng, start_lat = _route_end(form, "start", "Start", request.user)
            end_lng, end_lat = _route_end(form, "end", "End", request.user)

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
    address_labels = {option["id"]: option["label"] for option in address_options}

    selected_mode = next(
        (mode for mode in ROUTING_MODE_OPTIONS if mode["value"] == form["mode"]),
        ROUTING_MODE_OPTIONS[0],
    )

    start_name = _route_end_name(form, "start", address_labels)
    end_name = _route_end_name(form, "end", address_labels)
    homes = {str(pk) for pk in home_pin_ids(addresses)}
    route_name = (f"{_route_end_name(form, 'start', address_labels, homes)} → "
                  f"{_route_end_name(form, 'end', address_labels, homes)}")

    context = {
        "form": form,
        "address_options": address_options,
        "mode_options": ROUTING_MODE_OPTIONS,
        "selected_mode": selected_mode,
        "route": route,
        "route_json": json.dumps(route),
        "route_name": route_name[:200],
        # What the map's two markers say: the place's label, else the name.
        "point_labels": {
            "start": form["start_label"] if _is_resolved(form, "start") else start_name,
            "end": form["end_label"] if _is_resolved(form, "end") else end_name,
        },
        "start_resolved": _is_resolved(form, "start"),
        "end_resolved": _is_resolved(form, "end"),
        "geocoding_enabled": geocoding_enabled(geocoding_settings()),
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
    """The new-address form, opened blank or from a map click (?lat=&lng=).

    Nothing is looked up when the page opens (2026-09-28). The point used to
    be reverse-geocoded on every GET; that is a charged place lookup now, and
    opening a form is not asking for one. The point prefills the coordinates,
    and "Fill from the map point" asks the provider at its price, filling only
    the fields the member left empty.
    """
    initial_latitude = request.GET.get("lat")
    initial_longitude = request.GET.get("lng")

    if request.method == "POST":
        form = AddressCreateForm(request.POST)

        if form.is_valid():
            address = form.save(commit=False)
            address.created_by = request.user
            address.save()
            messages.success(request, _("Address saved."))
            return redirect("locations:address_detail", pk=address.pk)

    else:
        query_initial = {
            "country_name": request.GET.get("country", ""),
            "state_or_province_name": request.GET.get("region", ""),
            "locality_name": request.GET.get("locality", ""),
            "street": request.GET.get("street", ""),
            "building": request.GET.get("building", ""),
        }

        initial = {
            key: value
            for key, value in query_initial.items()
            if value not in (None, "")
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
        "geocoding_enabled": geocoding_enabled(geocoding_settings()),
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
        messages.error(request, _("Route name is required."))
        return redirect("locations:route_search")

    if not route_json:
        messages.error(request, _("No route geometry was provided."))
        return redirect("locations:route_search")

    try:
        payload = json.loads(route_json)
    except json.JSONDecodeError:
        messages.error(request, _("Route geometry is invalid."))
        return redirect("locations:route_search")

    # Accept either:
    # 1. {"type": "Feature", "geometry": {...}, "properties": {...}}
    # 2. {"type": "LineString", "coordinates": [...]}
    # 3. {"type": "MultiLineString", "coordinates": [...]}
    geometry_payload = payload.get("geometry") if payload.get("type") == "Feature" else payload

    if not geometry_payload:
        messages.error(request, _("Route geometry is missing."))
        return redirect("locations:route_search")

    try:
        geometry = GEOSGeometry(json.dumps(geometry_payload), srid=4326)
    except (TypeError, ValueError):
        messages.error(request, _("Route coordinates could not be converted."))
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

    from .access import readable_addresses

    start_address = None
    end_address = None
    # Only an address this member may read: a hidden one is a missing one.
    pickable = readable_addresses(request.user)

    if start_address_id and str(start_address_id).isdigit():
        start_address = pickable.filter(pk=start_address_id).first()

    if end_address_id and str(end_address_id).isdigit():
        end_address = pickable.filter(pk=end_address_id).first()

    route = Route.objects.create(
        name=name,
        geometry=route_geometry,
        start_address=start_address,
        end_address=end_address,
        created_by=request.user,
    )

    messages.success(request, f"Route '{route.name}' saved.")
    return redirect("locations:route_detail", pk=route.pk)


# ---------------------------------------------------------------------
# Geocoding (2026-09-28): charged per lookup, so POST with CSRF and never
# GET — a link, a prefetch or a crawler must not spend a member's mana.
# Both work on a GIS-off host too (urls.py exempts them).
# ---------------------------------------------------------------------

def _geocoding_refusal(exc, **extra):
    response = JsonResponse({"error": str(exc), **extra}, status=exc.status_code)
    if getattr(exc, "retry_after", None):
        response["Retry-After"] = str(exc.retry_after)
    return response


@require_POST
@login_required
def geocode_search(request):
    """Places by name or address: POST q -> {"results": [...], "cached"}."""
    try:
        answer = geocoding.search_answer(request.user, request.POST.get("q", ""))
    except geocoding.REFUSALS as exc:
        return _geocoding_refusal(exc)

    return JsonResponse({"results": answer.value, "cached": answer.cached})


@require_POST
@login_required
def geocode_reverse(request):
    """The address at a pin: POST lat, lng -> {"lat", "lng", "label",
    "fields", "found", "cached"}."""
    try:
        answer = geocoding.reverse_answer(
            request.user, request.POST.get("lat"), request.POST.get("lng"))
    except geocoding.REFUSALS as exc:
        return _geocoding_refusal(exc)

    return JsonResponse({**answer.value, "cached": answer.cached})


#: The map page's old search door keeps its URL and name, but it was a free
#: GET that sent every query to the provider; it is the charged search now.
location_search_api = geocode_search


# ---------------------------------------------------------------------
# Layer import
# ---------------------------------------------------------------------

@login_required
@require_POST
def api_import_layer(request):
    from collections import defaultdict
    from django.contrib.gis.geos import GEOSGeometry
    from .access import may_import_layer
    from .models import MapLayerPolygon

    # A layer is shared: every member sees it on the map. Importing one is a
    # staff act (2026-09-25).
    if not may_import_layer(request.user):
        return JsonResponse({"error": "Only staff may import map layers."}, status=403)

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
            from toto.vault.access import may_read
            from toto.vault.models import VaultFile
            vf = VaultFile.objects.get(pk=vault_file_id)
            # The pk arrives from the browser: the vault decides who may read.
            if not may_read(request.user, vf):
                raise VaultFile.DoesNotExist
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

    obj = _readable_or_404(request, model, pk)

    if kind == "routechain":
        geom_json = route_chain_geometry(obj, request.user)
    else:
        geom = getattr(obj, "geometry", None)
        geom_json = geometry_json(geom) if geom else None

    context = {
        "obj": obj,
        "object_label": str(obj),
        "object_type": model._meta.verbose_name.title(),
        "fields": [(label, value) for label, value in _detail_fields(kind, obj, request.user)],
        "geometry_json": json.dumps(geom_json),
        "has_geometry": geom_json is not None,
        "back_url": reverse("locations:locations_all"),
        "note_field": NOTE_FIELDS.get(kind),
        "note_value": getattr(obj, NOTE_FIELDS[kind], "") if kind in NOTE_FIELDS else "",
        "note_save_url": reverse("locations:note_save", args=[kind, pk]) if kind in NOTE_FIELDS else "",
        "can_edit": _may_write(request.user, obj),
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

    obj = _readable_or_404(request, model, pk)
    if not hasattr(obj, "metadata"):
        raise Http404(f"No metadata for kind '{kind}'.")
    from .access import may_write

    if not may_write(request.user, obj):
        return JsonResponse(
            {"status": "error", "error": "Only its creator or staff may change this."},
            status=403)
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

    obj = _readable_or_404(request, model, pk)
    from .access import may_write

    if not may_write(request.user, obj):
        raise PermissionDenied(_("Only its creator or staff may change this note."))
    setattr(obj, field, request.POST.get("note", "").strip())
    obj.save(update_fields=[field])
    messages.success(request, _("Note saved."))

    # A posted `next` only when it stays on this site (2026-09-30).
    return redirect(safe_next(
        request, request.POST.get("next"),
        reverse("locations:location_detail", args=[kind, pk]),
    ))


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

# ---------------------------------------------------------------------
# People — who lives near you
# ---------------------------------------------------------------------

#: What the radius slider offers. Bounded by nearby.MAX_RADIUS_KM, which is a
#: privacy limit rather than a performance one — see that module.
RADIUS_CHOICES_KM = (1, 2, 5, 10, 25, 50, 100)

DEFAULT_RADIUS_KM = 10


@login_required
def people(request):
    """People who have chosen to be findable, near a point.

    Every person on this page put themselves here. There is no view of somebody
    who did not switch sharing on — not for staff, not for a fellow community
    member, not for the person's own patron. See `people_access`.
    """
    from toto.socialhub.models import Community

    from . import nearby
    from .people_access import place_label, point_for

    viewer = current_person(request)

    centre, centre_label = _search_centre(request, viewer)
    radius_km = _requested_radius(request)

    # Only what the viewer may see listed: a clearance is not a filter a member
    # can pick or type (2026-09-28) — it would say who is in it.
    listed = Community.objects.all()
    community = None
    community_id = (request.GET.get("community") or "").strip()
    if community_id.isdigit():
        community = listed.filter(pk=int(community_id)).first()

    results = []
    if centre is not None:
        for person, distance in nearby.people_within(
                request.user, latitude=centre[0], longitude=centre[1],
                radius_km=radius_km, community=community):
            point = point_for(person)
            results.append({
                "person": person,
                "distance_km": round(distance, 1),
                "latitude": point[0],
                "longitude": point[1],
                "place": place_label(person),
                "approximate": person.location_sharing == "approximate",
            })

    return render(request, "locations/people.html", PageProcessor().decorate({
        "results": results,
        "centre": centre,
        "centre_label": centre_label,
        "radius_km": radius_km,
        "radius_choices": RADIUS_CHOICES_KM,
        "communities": listed.order_by("name"),
        "selected_community": community,
        # Whether the VIEWER is on the map themselves. A page that lets you
        # search for others while you are invisible is worth saying out loud,
        # not because it is forbidden but because most people assume the
        # opposite.
        "viewer_shares": bool(viewer and viewer.location_sharing != "off"),
        "viewer_has_address": bool(viewer and viewer.address_id),
    }, request))


def _requested_radius(request) -> int:
    raw = (request.GET.get("radius") or "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RADIUS_KM
    return value if value in RADIUS_CHOICES_KM else DEFAULT_RADIUS_KM


def _search_centre(request, viewer):
    """Where to measure from: a dropped pin, else the viewer's own address.

    Returns `((lat, lon) | None, label)`. None means we have nowhere to measure
    from — the page says so rather than silently listing nobody, because "no
    results" and "we do not know where you are" look identical otherwise.
    """
    from .people_access import _coordinates

    raw_lat = (request.GET.get("lat") or "").strip()
    raw_lon = (request.GET.get("lon") or "").strip()
    if raw_lat and raw_lon:
        try:
            lat, lon = float(raw_lat), float(raw_lon)
        except ValueError:
            lat = lon = None
        else:
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return (lat, lon), ""

    if viewer is not None and viewer.address_id:
        lat, lon = _coordinates(viewer.address)
        if lat is not None and lon is not None:
            return (lat, lon), str(viewer.address)

    return None, ""
