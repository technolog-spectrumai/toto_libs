import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator

from toto.api.cors import CorsApiView


@method_decorator(csrf_exempt, name="dispatch")
class FleetApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            from toto.inventory.models import RealWorldObject
            qs = (
                RealWorldObject.objects
                .filter(object_type__is_mobile=True)
                .select_related("object_type", "owner", "custodian", "location")
                .prefetch_related("leases")
                .order_by("object_type__category", "name")
            )
            items = []
            features = []
            for obj in qs:
                active_lease = next((lse for lse in obj.leases.all() if lse.status == "active"), None)
                geom = None
                if obj.location and obj.location.geometry:
                    try:
                        geom = json.loads(obj.location.geometry.geojson)
                    except Exception:
                        pass
                item = {
                    "id": obj.pk,
                    "name": obj.name,
                    "object_type": str(obj.object_type) if obj.object_type else "",
                    "category": obj.object_type.category if obj.object_type else "other",
                    "category_display": obj.object_type.get_category_display() if obj.object_type else "Other",
                    "owner": str(obj.owner) if obj.owner else None,
                    "custodian": str(obj.custodian) if obj.custodian else None,
                    "location_label": str(obj.location) if obj.location else None,
                    "has_location": bool(geom),
                    "lease_status": active_lease.status if active_lease else None,
                }
                items.append(item)
                if geom:
                    features.append({
                        "type": "Feature",
                        "geometry": geom,
                        "properties": {**item, "layer": "fleet"},
                    })
            return JsonResponse({
                "items": items,
                "geojson": {"type": "FeatureCollection", "features": features},
            })
        except Exception as exc:
            return JsonResponse({"items": [], "geojson": {"type": "FeatureCollection", "features": []}, "error": str(exc)})


@method_decorator(csrf_exempt, name="dispatch")
class PackagesApiView(CorsApiView):
    def get(self, request):
        if not request.user or not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated."}, status=401)
        try:
            from toto.logistics.models import Package
            qs = (
                Package.objects
                .select_related("origin", "destination", "transport")
                .order_by("-created_at")[:100]
            )
            result = []
            for pkg in qs:
                result.append({
                    "id": pkg.pk,
                    "tracking_number": pkg.tracking_number,
                    "status": pkg.status,
                    "status_display": pkg.get_status_display(),
                    "description": pkg.description or "",
                    "origin": str(pkg.origin) if pkg.origin else None,
                    "destination": str(pkg.destination) if pkg.destination else None,
                    "transport": str(pkg.transport) if pkg.transport else None,
                    "estimated_delivery": pkg.estimated_delivery.isoformat() if getattr(pkg, "estimated_delivery", None) else None,
                })
            return JsonResponse({"packages": result})
        except Exception as exc:
            return JsonResponse({"packages": [], "error": str(exc)})
