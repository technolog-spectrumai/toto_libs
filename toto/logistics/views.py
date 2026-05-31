import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from toto.ui import PageProcessor

from .models import Package, PackageStatus


def _geom(address):
    if address and address.geometry:
        return json.loads(address.geometry.geojson)
    return None


def _package_payload(pkg):
    events = list(
        pkg.events.select_related("location", "transport").order_by("occurred_at")
    )
    return {
        "id": pkg.pk,
        "tracking_number": pkg.tracking_number,
        "carrier": pkg.carrier,
        "status": pkg.status,
        "status_display": pkg.get_status_display(),
        "origin": {"label": str(pkg.origin), "geometry": _geom(pkg.origin)} if pkg.origin else None,
        "destination": {"label": str(pkg.destination), "geometry": _geom(pkg.destination)} if pkg.destination else None,
        "events": [
            {
                "status": e.status,
                "status_display": e.get_status_display(),
                "note": e.note,
                "occurred_at": e.occurred_at.isoformat(),
                "transport": str(e.transport) if e.transport else None,
                "location": {
                    "label": str(e.location),
                    "geometry": _geom(e.location),
                } if e.location else None,
            }
            for e in events
        ],
    }


def package_tracking(request, tracking_number):
    """Public tracking page — no login required."""
    pkg = get_object_or_404(
        Package.objects.select_related(
            "origin", "destination", "transport", "shipment"
        ).prefetch_related("events__location", "events__transport"),
        tracking_number=tracking_number,
    )
    payload = _package_payload(pkg)
    ctx = {
        "package": pkg,
        "payload_json": json.dumps(payload),
        "events": pkg.events.select_related("location", "transport").order_by("occurred_at"),
    }
    return render(request, "logistics/package_tracking.html", PageProcessor().decorate(ctx, request))


def fleet(request):
    from toto.inventory.models import RealWorldObject
    category = request.GET.get("category") or None
    qs = (
        RealWorldObject.objects
        .filter(object_type__is_mobile=True)
        .select_related("object_type", "owner", "custodian", "location")
        .prefetch_related("leases")
        .order_by("object_type__category", "name")
    )
    if category:
        qs = qs.filter(object_type__category=category)
    from toto.inventory.models import ObjectCategory
    return render(
        request,
        "logistics/fleet.html",
        PageProcessor().decorate({
            "objects": qs,
            "category": category,
            "category_choices": ObjectCategory.choices,
            "total": qs.count(),
        }, request),
    )


def fleet_geojson(request):
    """GeoJSON FeatureCollection of all mobile inventory objects that have a location.

    The response is a standard GeoJSON file that can be:
    - consumed directly by the fleet map view
    - downloaded and imported into the locations app (Locations → Import Layer)
    """
    from toto.inventory.models import RealWorldObject

    qs = (
        RealWorldObject.objects
        .filter(object_type__is_mobile=True, location__geometry__isnull=False)
        .select_related("object_type", "owner", "custodian", "location")
        .prefetch_related("leases")
    )

    features = []
    for obj in qs:
        geom = json.loads(obj.location.geometry.geojson)
        active_lease = next(
            (l for l in obj.leases.all() if l.status == "active"), None
        )
        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "id": obj.pk,
                "name": obj.name,
                "object_type": str(obj.object_type) if obj.object_type else "",
                "category": obj.object_type.category if obj.object_type else "",
                "category_display": obj.object_type.get_category_display() if obj.object_type else "",
                "is_mobile": True,
                "owner": str(obj.owner) if obj.owner else None,
                "custodian": str(obj.custodian) if obj.custodian else None,
                "location_label": str(obj.location),
                "lease_status": active_lease.status if active_lease else None,
                "lease_status_display": active_lease.get_status_display() if active_lease else None,
                "layer": "fleet",
            },
        })

    return JsonResponse({"type": "FeatureCollection", "features": features})


def fleet_map(request):
    from toto.inventory.models import RealWorldObject, ObjectCategory

    category = request.GET.get("category") or None
    qs = (
        RealWorldObject.objects
        .filter(object_type__is_mobile=True)
        .select_related("object_type", "owner", "location")
    )
    if category:
        qs = qs.filter(object_type__category=category)

    total = qs.count()
    located = qs.filter(location__geometry__isnull=False).count()

    return render(
        request,
        "logistics/fleet_map.html",
        PageProcessor().decorate({
            "total": total,
            "located": located,
            "category": category,
            "category_choices": ObjectCategory.choices,
        }, request),
    )


def package_list(request):
    """Staff listing of all packages."""
    packages = Package.objects.select_related("origin", "destination", "transport").order_by("-created_at")
    return render(
        request,
        "logistics/package_list.html",
        PageProcessor().decorate({"packages": packages, "statuses": PackageStatus.choices}, request),
    )
