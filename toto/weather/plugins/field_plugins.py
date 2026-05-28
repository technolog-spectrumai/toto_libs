"""
Field Command plugins for the Weather app.
Registered in WeatherConfig.ready().
"""
import json


# ---------------------------------------------------------------------------
# Map features
# ---------------------------------------------------------------------------

def weather_map_features(request=None):
    from django.db.models import Subquery, OuterRef
    from toto.weather.models import WeatherObservation
    from toto.locations.models import Address

    latest_id = (
        WeatherObservation.objects
        .filter(address=OuterRef("pk"), temperature__isnull=False)
        .order_by("-loaded_at")
        .values("id")[:1]
    )
    features = []
    for a in Address.objects.exclude(geometry=None).annotate(obs_id=Subquery(latest_id)).filter(obs_id__isnull=False):
        try:
            obs = WeatherObservation.objects.get(pk=a.obs_id)
        except WeatherObservation.DoesNotExist:
            continue
        features.append({
            "type": "Feature",
            "geometry": json.loads(a.geometry.geojson),
            "properties": {
                "layer": "weather",
                "name": str(a),
                "temperature": obs.temperature,
                "wind_speed_kmh": obs.wind_speed_kmh,
                "cloud_cover": obs.cloud_cover,
                "precipitation_mm": obs.precipitation_mm,
                "id": a.pk,
            },
        })
    return features


# ---------------------------------------------------------------------------
# Metrics section
# ---------------------------------------------------------------------------

def weather_metrics_section(request=None):
    from toto.weather.models import WeatherObservation, ForecastSession

    obs_count = WeatherObservation.objects.count()
    covered = WeatherObservation.objects.values("address").distinct().count()
    sessions = ForecastSession.objects.count()

    return {
        "key": "weather",
        "title": "Weather",
        "order": 20,
        "app_url": "/weather/",
        "ribbon": [
            {"label": "Observations", "value": obs_count, "alert": False},
        ],
        "kpis": [
            {"label": "Observations", "value": obs_count, "sub": "stored", "alert": False},
            {"label": "Covered Locations", "value": covered, "sub": "addresses", "alert": False},
            {"label": "Forecast Sessions", "value": sessions, "sub": "loaded", "alert": False},
        ],
        "chart": None,
        "table": None,
    }
