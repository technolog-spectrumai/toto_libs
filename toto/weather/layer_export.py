"""
Export recent weather observations as MapLayer overlays in the locations app.

Each function picks the most recent WeatherObservation per address for one
metric and rebuilds the corresponding MapLayer polygons.
"""
from django.db.models import Subquery, OuterRef

from .models import WeatherObservation
from toto.locations.layer_export import refresh_layer_from_addresses
from toto.locations.models import Address

LAYER_TEMPERATURE_SLUG = "layer-weather-temperature"
LAYER_PRECIPITATION_SLUG = "layer-weather-precipitation"
LAYER_WIND_SLUG = "layer-weather-wind-speed"
LAYER_CLOUD_SLUG = "layer-weather-cloud-cover"

LAYER_EXPORT_SLUGS = [
    LAYER_TEMPERATURE_SLUG,
    LAYER_PRECIPITATION_SLUG,
    LAYER_WIND_SLUG,
    LAYER_CLOUD_SLUG,
]


def _addresses_with_latest(field):
    """Annotate addresses with the most recent non-null value for `field`."""
    latest = (
        WeatherObservation.objects
        .filter(address=OuterRef("pk"), **{f"{field}__isnull": False})
        .order_by("-loaded_at")
        .values(field)[:1]
    )
    return (
        Address.objects
        .filter(geometry__isnull=False)
        .annotate(obs_value=Subquery(latest))
        .filter(obs_value__isnull=False)
    )


def export_temperature_layer():
    entries = [
        (addr, addr.obs_value, {"field": "temperature", "address_id": addr.pk})
        for addr in _addresses_with_latest("temperature")
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_TEMPERATURE_SLUG,
        name="Weather — Temperature",
        unit="°C",
        description="Latest observed temperature per location.",
        entries=entries,
    )


def export_precipitation_layer():
    entries = [
        (addr, addr.obs_value, {"field": "precipitation_mm", "address_id": addr.pk})
        for addr in _addresses_with_latest("precipitation_mm")
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_PRECIPITATION_SLUG,
        name="Weather — Precipitation",
        unit="mm",
        description="Latest observed precipitation per location.",
        entries=entries,
    )


def export_wind_layer():
    entries = [
        (addr, addr.obs_value, {"field": "wind_speed_kmh", "address_id": addr.pk})
        for addr in _addresses_with_latest("wind_speed_kmh")
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_WIND_SLUG,
        name="Weather — Wind Speed",
        unit="km/h",
        description="Latest observed wind speed per location.",
        entries=entries,
    )


def export_cloud_cover_layer():
    entries = [
        (addr, addr.obs_value, {"field": "cloud_cover", "address_id": addr.pk})
        for addr in _addresses_with_latest("cloud_cover")
    ]
    return refresh_layer_from_addresses(
        slug=LAYER_CLOUD_SLUG,
        name="Weather — Cloud Cover",
        unit="%",
        description="Latest observed cloud cover per location.",
        entries=entries,
    )
