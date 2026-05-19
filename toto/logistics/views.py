import json

from django.shortcuts import get_object_or_404, render
from django.views.generic import ListView
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


def package_list(request):
    """Staff listing of all packages."""
    packages = Package.objects.select_related("origin", "destination", "transport").order_by("-created_at")
    return render(
        request,
        "logistics/package_list.html",
        PageProcessor().decorate({"packages": packages, "statuses": PackageStatus.choices}, request),
    )
