import json

from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from oya.page import PageProcessor
from .models import PointFeature, ZoneFeature, PathFeature, Address


@login_required
def locations_all(request):
    locations = []

    for p in PointFeature.objects.all():
        locations.append({
            "type": "Point",
            "name": p.name or f"Point {p.pk}",
            "geometry": json.loads(p.geometry.geojson) if p.geometry else None,
        })

    for z in ZoneFeature.objects.all():
        locations.append({
            "type": "Zone",
            "name": z.name or f"Zone {z.pk}",
            "geometry": json.loads(z.geometry.geojson) if z.geometry else None,
        })

    for path in PathFeature.objects.all():
        locations.append({
            "type": "Path",
            "name": path.name or f"Path {path.pk}",
            "geometry": json.loads(path.geometry.geojson) if path.geometry else None,
        })

    for addr in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(addr),
            "geometry": json.loads(addr.location.geojson) if addr.location else None,
        })

    context = {
        "locations": locations,
        "locations_json": json.dumps(locations),
    }

    return render(
        request,
        "locations/locations.html",
        PageProcessor().decorate(context, request)
    )


