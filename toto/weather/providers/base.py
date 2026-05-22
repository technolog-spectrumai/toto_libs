from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toto.locations.models import Address


class BaseWeatherProvider:
    slug: str = ""
    name: str = ""
    forecast_resolution_hours: int = 1

    def fetch_current(self, addresses: list["Address"]) -> list[dict]:
        """
        Fetch current weather for a list of Address objects.
        Returns a list of dicts with keys:
            address_id, observed_at, temperature, precipitation_mm,
            precipitation_type, cloud_cover, wind_speed_kmh,
            wind_direction_deg, visibility_km.
        On error for a specific address, include {'address_id': ..., 'error': '...'}.
        """
        raise NotImplementedError

    def fetch_forecast(self, addresses: list["Address"], start_at=None, end_at=None) -> dict:
        """
        Fetch hourly forecast for a list of Address objects.
        Returns a dict with keys:
            valid_from (ISO str), valid_to (ISO str),
            resolution_hours (int), points (list of dicts).
        Each point dict has the same keys as fetch_current plus valid_at.
        """
        raise NotImplementedError
