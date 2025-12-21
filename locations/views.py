import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from oya.page import PageProcessor
from .models import PointFeature, ZoneFeature, PathFeature, Address


@login_required
def locations_all(request):
    locations = []

    # Points
    for p in PointFeature.objects.all():
        locations.append({
            "type": "Point",
            "name": p.name or f"Point {p.pk}",
            "geometry": json.loads(p.geometry.geojson) if p.geometry else None,
            "geometry_json": json.loads(p.geometry.geojson if p.geometry else None),
        })

    # Zones (polygons)
    for z in ZoneFeature.objects.all():
        locations.append({
            "type": "Zone",
            "name": z.name or f"Zone {z.pk}",
            "geometry": json.loads(z.geometry.geojson) if z.geometry else None,
            "geometry_json": json.loads(z.geometry.geojson if z.geometry else None),
        })

    # Paths (multiline)
    for path in PathFeature.objects.all():
        locations.append({
            "type": "Path",
            "name": path.name or f"Path {path.pk}",
            "geometry": json.loads(path.geometry.geojson) if path.geometry else None,
            "geometry_json": json.loads(path.geometry.geojson if path.geometry else None),
        })

    # Addresses (points)
    for addr in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(addr),
            "geometry": json.loads(addr.location.geojson) if addr.location else None,
            "geometry_json": json.dumps(addr.location.geojson if addr.location else None),
        })

    context = {
        "locations": locations,
        "locations_json": json.dumps(locations),  # for Leaflet
    }

    return render(
        request,
        "locations/locations.html",
        PageProcessor().decorate(context, request)
    )
