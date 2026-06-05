"""
Locations map-layer providers for the Weather app.
Registered in WeatherConfig.ready().

Each function returns a layer dict compatible with map_layer_payload format.
Address Points are buffered to small polygon circles so the overlay renderer
can fill and color them.
"""
from __future__ import annotations

import json

_BUFFER_DEG = 0.25  # ~25 km at equator — city-scale circles for address points


def _to_polygon_geojson(geom) -> dict:
    """Return a Polygon/MultiPolygon GeoJSON dict; buffer non-polygon geometries."""
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return json.loads(geom.geojson)
    return json.loads(geom.buffer(_BUFFER_DEG).geojson)


def weather_temperature_layer() -> dict | None:
    """Latest temperature observation per address, as a map overlay layer."""
    try:
        from django.db.models import OuterRef, Subquery
        from toto.locations.models import Address
        from toto.weather.models import WeatherObservation

        latest_obs_id = (
            WeatherObservation.objects
            .filter(address=OuterRef("pk"), temperature__isnull=False)
            .order_by("-loaded_at")
            .values("id")[:1]
        )
        addresses = list(
            Address.objects
            .exclude(geometry=None)
            .annotate(obs_id=Subquery(latest_obs_id))
            .filter(obs_id__isnull=False)
        )
        if not addresses:
            return None

        obs_map = {
            o.pk: o
            for o in WeatherObservation.objects.filter(
                pk__in=[a.obs_id for a in addresses]
            )
        }

        polygons, values = [], []
        for addr in addresses:
            obs = obs_map.get(addr.obs_id)
            if not obs or obs.temperature is None:
                continue
            val = float(obs.temperature)
            values.append(val)
            polygons.append({
                "id": f"wt-{addr.pk}",
                "name": str(addr),
                "value": val,
                "properties": {
                    "wind_speed_kmh": obs.wind_speed_kmh,
                    "cloud_cover": obs.cloud_cover,
                    "precipitation_mm": obs.precipitation_mm,
                    "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
                    "provider": obs.provider,
                },
                "center": json.loads(addr.geometry.geojson),
                "geometry": _to_polygon_geojson(addr.geometry),
            })

        if not polygons:
            return None

        return {
            "id": "weather-temperature",
            "name": "Temperature",
            "slug": "weather-temperature",
            "description": "Latest observed temperature at each monitored address.",
            "unit": "°C",
            "min_value": min(values),
            "max_value": max(values),
            "style": {},
            "inverted_importance": False,
            "half_range": False,
            "polygons": polygons,
        }
    except Exception:
        import logging
        logging.getLogger("weather.layers").exception("weather_temperature_layer failed")
        return None


def weather_precipitation_layer() -> dict | None:
    """Latest precipitation observation per address, as a map overlay layer."""
    try:
        from django.db.models import OuterRef, Subquery
        from toto.locations.models import Address
        from toto.weather.models import WeatherObservation

        latest_obs_id = (
            WeatherObservation.objects
            .filter(address=OuterRef("pk"), precipitation_mm__isnull=False)
            .order_by("-loaded_at")
            .values("id")[:1]
        )
        addresses = list(
            Address.objects
            .exclude(geometry=None)
            .annotate(obs_id=Subquery(latest_obs_id))
            .filter(obs_id__isnull=False)
        )
        if not addresses:
            return None

        obs_map = {
            o.pk: o
            for o in WeatherObservation.objects.filter(
                pk__in=[a.obs_id for a in addresses]
            )
        }

        polygons, values = [], []
        for addr in addresses:
            obs = obs_map.get(addr.obs_id)
            if not obs or obs.precipitation_mm is None:
                continue
            val = float(obs.precipitation_mm)
            values.append(val)
            polygons.append({
                "id": f"wp-{addr.pk}",
                "name": str(addr),
                "value": val,
                "properties": {
                    "temperature": obs.temperature,
                    "wind_speed_kmh": obs.wind_speed_kmh,
                    "cloud_cover": obs.cloud_cover,
                    "observed_at": obs.observed_at.isoformat() if obs.observed_at else None,
                    "provider": obs.provider,
                },
                "center": json.loads(addr.geometry.geojson),
                "geometry": _to_polygon_geojson(addr.geometry),
            })

        if not polygons:
            return None

        return {
            "id": "weather-precipitation",
            "name": "Precipitation",
            "slug": "weather-precipitation",
            "description": "Latest observed precipitation at each monitored address.",
            "unit": "mm",
            "min_value": 0.0,
            "max_value": max(values) or 1.0,
            "style": {},
            "inverted_importance": False,
            "half_range": False,
            "polygons": polygons,
        }
    except Exception:
        import logging
        logging.getLogger("weather.layers").exception("weather_precipitation_layer failed")
        return None
