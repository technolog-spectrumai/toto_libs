import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView, MeshGatedApiView
from toto.locations.models import Address, Zone, Territory, Route, RouteChain, MapLayer


def _geom(geometry):
    if geometry is None:
        return None
    try:
        return json.loads(geometry.geojson)
    except Exception:
        return None


def _address_to_dict(addr):
    lat, lng = None, None
    if addr.geometry:
        try:
            lat = addr.geometry.y
            lng = addr.geometry.x
        except Exception:
            pass
    return {
        "id": addr.id,
        "street": addr.street,
        "building": addr.building,
        "apartment": addr.apartment,
        "locality_name": addr.locality_name,
        "state_or_province_name": addr.state_or_province_name,
        "country_name": addr.country_name,
        "lat": lat,
        "lng": lng,
        "display": str(addr),
    }


def _zone_to_dict(zone):
    return {
        "id": zone.id,
        "name": zone.name,
        "territory_name": zone.territory.name if zone.territory else None,
    }


@method_decorator(csrf_exempt, name="dispatch")
class ZoneListApiView(MeshGatedApiView):
    def get(self, request):
        zones = Zone.objects.select_related("territory").order_by("name")[:200]
        return JsonResponse({"zones": [_zone_to_dict(z) for z in zones]})


@method_decorator(csrf_exempt, name="dispatch")
class AddressListCreateApiView(MeshGatedApiView):
    def get(self, request):
        addresses = Address.objects.order_by("locality_name", "street")[:200]
        return JsonResponse({"addresses": [_address_to_dict(a) for a in addresses]})

    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        address = Address.objects.create(
            street=data.get("street", ""),
            building=data.get("building", ""),
            apartment=data.get("apartment", ""),
            locality_name=data.get("locality_name", ""),
            state_or_province_name=data.get("state_or_province_name", ""),
            country_name=data.get("country_name", "")[:2] if data.get("country_name") else "",
        )
        return JsonResponse(_address_to_dict(address), status=201)


@method_decorator(csrf_exempt, name="dispatch")
class AddressDetailApiView(CorsApiView):
    def get(self, request, pk):
        try:
            address = Address.objects.get(pk=pk)
        except Address.DoesNotExist:
            return JsonResponse({"error": "Address not found."}, status=404)
        return JsonResponse(_address_to_dict(address))


@method_decorator(csrf_exempt, name="dispatch")
class MapDataApiView(MeshGatedApiView):
    def get(self, request):
        locations = []

        for obj in Territory.objects.select_related("capital").all():
            locations.append({
                "type": "Territory",
                "name": obj.name or f"Territory {obj.pk}",
                "detail": f"Capital: {obj.capital}" if obj.capital else "Territory",
                "geometry": _geom(obj.geometry),
            })

        for obj in Zone.objects.select_related("territory").all():
            locations.append({
                "type": "Zone",
                "name": obj.name or f"Zone {obj.pk}",
                "detail": f"Inside {obj.territory.name}" if obj.territory else "Standalone zone",
                "geometry": _geom(obj.geometry),
            })

        for chain in RouteChain.objects.prefetch_related("routes").all():
            coords = []
            for r in chain.routes.order_by("sequence", "name", "pk"):
                g = _geom(r.geometry)
                if not g:
                    continue
                if g["type"] == "MultiLineString":
                    coords.extend(g["coordinates"])
                elif g["type"] == "LineString":
                    coords.append(g["coordinates"])
            geometry = {"type": "MultiLineString", "coordinates": coords} if coords else None
            locations.append({
                "type": "Route Chain",
                "name": chain.name or f"Route Chain {chain.pk}",
                "detail": chain.description or f"{chain.routes.count()} routes",
                "geometry": geometry,
            })

        for obj in Route.objects.select_related("route_chain", "start_address", "end_address").all():
            locations.append({
                "type": "Route",
                "name": obj.name or f"Route {obj.pk}",
                "detail": f"In {obj.route_chain.name}" if obj.route_chain else "Route",
                "geometry": _geom(obj.geometry),
            })

        for obj in Address.objects.all():
            locations.append({
                "type": "Address",
                "name": str(obj),
                "detail": obj.locality_name,
                "geometry": _geom(obj.geometry),
                "lat": obj.geometry.y if obj.geometry else None,
                "lng": obj.geometry.x if obj.geometry else None,
            })

        return JsonResponse({"locations": locations})


@method_decorator(csrf_exempt, name="dispatch")
class MapLayersApiView(MeshGatedApiView):
    def get(self, request):
        layers = []
        for layer in MapLayer.objects.filter(is_active=True).prefetch_related("polygons").order_by("name"):
            layers.append({
                "id": layer.pk,
                "name": layer.name,
                "slug": layer.slug,
                "unit": layer.unit or "",
                "min_value": layer.min_value,
                "max_value": layer.max_value,
                "style": layer.style or {},
                "inverted_importance": layer.inverted_importance,
                "half_range": layer.half_range,
                "polygons": [
                    {
                        "id": p.pk,
                        "name": p.name or f"Polygon {p.pk}",
                        "value": p.value,
                        "center": _geom(p.center) if hasattr(p, "center") and p.center else None,
                        "geometry": _geom(p.geometry),
                    }
                    for p in layer.polygons.all()
                    if p.geometry
                ],
            })
        return JsonResponse({"layers": layers})


@method_decorator(csrf_exempt, name="dispatch")
class RouteSearchApiView(CorsApiView):
    def post(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            data = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON."}, status=400)

        from toto.locations.views import fetch_traversable_route, parse_coordinate, ROUTING_MODE_OPTIONS

        mode = data.get("mode", "car")
        if mode not in {m["value"] for m in ROUTING_MODE_OPTIONS}:
            return JsonResponse({"error": "Invalid mode."}, status=400)

        try:
            start_lat = parse_coordinate(data.get("start_lat"), "Start latitude", -90, 90)
            start_lng = parse_coordinate(data.get("start_lng"), "Start longitude", -180, 180)
            end_lat = parse_coordinate(data.get("end_lat"), "End latitude", -90, 90)
            end_lng = parse_coordinate(data.get("end_lng"), "End longitude", -180, 180)
            result = fetch_traversable_route(start_lng, start_lat, end_lng, end_lat, mode)
            return JsonResponse(result)
        except ValueError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
