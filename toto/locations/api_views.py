import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.telegraph.api_views import CorsApiView
from toto.locations.models import Address, Zone


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
class ZoneListApiView(CorsApiView):
    def get(self, request):
        zones = Zone.objects.select_related("territory").order_by("name")[:200]
        return JsonResponse({"zones": [_zone_to_dict(z) for z in zones]})


@method_decorator(csrf_exempt, name="dispatch")
class AddressListCreateApiView(CorsApiView):
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
