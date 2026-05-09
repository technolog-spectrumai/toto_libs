import json
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from toto.core.page import PageProcessor
from .models import (
    Territory,
    Zone,
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
            "detail": f"Capital: {t.capital}" if t.capital else "Territory",
            "geometry": json.loads(t.geometry.geojson) if t.geometry else None,
            "geometry_json": json.loads(t.geometry.geojson) if t.geometry else None,
        })

    # Zones (multipolygons)
    for z in Zone.objects.all():
        locations.append({
            "type": "Zone",
            "name": z.name or f"Zone {z.pk}",
            "detail": f"Inside {z.territory.name}" if z.territory else "Standalone zone",
            "geometry": json.loads(z.geometry.geojson) if z.geometry else None,
            "geometry_json": json.loads(z.geometry.geojson) if z.geometry else None,
        })

    # Routes (multiline)
    for r in Route.objects.all():
        locations.append({
            "type": "Route",
            "name": r.name or f"Route {r.pk}",
            "detail": "Route",
            "geometry": json.loads(r.geometry.geojson) if r.geometry else None,
            "geometry_json": json.loads(r.geometry.geojson) if r.geometry else None,
        })

    # Addresses (points)
    for addr in Address.objects.all():
        locations.append({
            "type": "Address",
            "name": str(addr),
            "detail": addr.locality_name,
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
