"""
Field Command plugins for the Weather app.
Registered in WeatherConfig.ready().
"""
import json
from django.utils.translation import gettext_lazy as _


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
        "title": _("Weather"),
        "order": 20,
        "app_url": "/weather/",
        "ribbon": [
            {"label": _("Observations"), "value": obs_count, "alert": False},
        ],
        "kpis": [
            {"label": _("Observations"), "value": obs_count, "sub": _("stored"), "alert": False},
            {"label": _("Covered Locations"), "value": covered, "sub": _("addresses"), "alert": False},
            {"label": _("Forecast Sessions"), "value": sessions, "sub": _("loaded"), "alert": False},
        ],
        "chart": None,
        "table": None,
    }
