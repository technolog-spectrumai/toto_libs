import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from toto.core.page import PageProcessor
from .models import (
    Territory,
    Route,
    Address
)


@login_required
def locations_all(request):
    locations = []

    # Territories (polygons)
    for t in Territory.objects.all():
        locations.append({
            "type": "Territory",
            "name": t.name or f"Territory {t.pk}",
            "geometry": json.loads(t.geometry.geojson) if t.geometry else None,
            "geometry_json": json.loads(t.geometry.geojson) if t.geometry else None,
        })

    # Routes (multiline)
    for r in Route.objects.all():
        locations.append({
            "type": "Route",
            "name": r.name or f"Route {r.pk}",
            "geometry": json.loads(r.geometry.geojson) if r.geometry else None,
            "geometry_json": json.loads(r.geometry.geojson) if r.geometry else None,
        })

    # Addresses (points)
    for addr in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(addr),
            "geometry": json.loads(addr.geometry.geojson) if addr.geometry else None,
            "geometry_json": json.loads(addr.geometry.geojson) if addr.geometry else None,
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
