"""
Open-Meteo weather provider (free, no API key required).
Docs: https://open-meteo.com/en/docs
"""
from __future__ import annotations

import logging

import requests

from .base import BaseWeatherProvider

log = logging.getLogger(__name__)

_BASE_URL = "https://api.open-meteo.com/v1/forecast"

_CURRENT_VARS = [
    "temperature_2m",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "visibility",
]

_HOURLY_VARS = [
    "temperature_2m",
    "precipitation",
    "weather_code",
    "cloud_cover",
    "wind_speed_10m",
    "wind_direction_10m",
    "visibility",
]


def _wmo_to_precip_type(code) -> str:
    if code is None:
        return ""
    c = int(code)
    if 51 <= c <= 57:
        return "drizzle"
    if c in (61, 63, 65, 80, 81, 82):
        return "rain"
    if c in (71, 73, 75, 77, 85, 86):
        return "snow"
    if c in (66, 67):
        return "sleet"
    return ""


class OpenMeteoProvider(BaseWeatherProvider):
    slug = "open_meteo"
    name = "Open-Meteo (Free)"
    forecast_resolution_hours = 1

    def fetch_current(self, addresses) -> list[dict]:
        results = []
        for address in addresses:
            if not address.geometry:
                continue
            lat = address.geometry.y
            lng = address.geometry.x
            try:
                resp = requests.get(
                    _BASE_URL,
                    params={
                        "latitude": lat,
                        "longitude": lng,
                        "current": ",".join(_CURRENT_VARS),
                        "wind_speed_unit": "kmh",
                        "timezone": "UTC",
                    },
                    timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                cur = data.get("current") or {}
                vis_raw = cur.get("visibility")
                results.append({
                    "address_id": address.id,
                    "observed_at": cur.get("time"),
                    "temperature": cur.get("temperature_2m"),
                    "precipitation_mm": cur.get("precipitation"),
                    "precipitation_type": _wmo_to_precip_type(cur.get("weather_code")),
                    "cloud_cover": cur.get("cloud_cover"),
                    "wind_speed_kmh": cur.get("wind_speed_10m"),
                    "wind_direction_deg": cur.get("wind_direction_10m"),
                    "visibility_km": round(vis_raw / 1000, 2) if vis_raw is not None else None,
                })
            except Exception as exc:
                log.warning("Open-Meteo current weather failed for address %s: %s", address.id, exc)
                results.append({"address_id": address.id, "error": str(exc)})
        return results

    def fetch_forecast(self, addresses, start_at=None, end_at=None) -> dict:
        from datetime import datetime, timezone as tz, timedelta
        now = datetime.now(tz.utc)
        if start_at is None:
            start_at = now
        if end_at is None:
            end_at = now + timedelta(hours=24)

        hours_ahead = max(1, int((end_at - now).total_seconds() / 3600) + 1)

        all_points: list[dict] = []
        valid_from = None
        valid_to = None

        for address in addresses:
            if not address.geometry:
                continue
            lat = address.geometry.y
            lng = address.geometry.x
            try:
                resp = requests.get(
                    _BASE_URL,
                    params={
                        "latitude": lat,
                        "longitude": lng,
                        "hourly": ",".join(_HOURLY_VARS),
                        "forecast_hours": hours_ahead,
                        "wind_speed_unit": "kmh",
                        "timezone": "UTC",
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                hourly = data.get("hourly") or {}
                times = hourly.get("time") or []

                def _val(key, i):
                    lst = hourly.get(key)
                    if lst and i < len(lst):
                        return lst[i]
                    return None

                for i, t in enumerate(times):
                    # t is "YYYY-MM-DDTHH:MM" (UTC, no tz suffix from Open-Meteo)
                    t_dt = datetime.fromisoformat(t).replace(tzinfo=tz.utc)
                    if t_dt < start_at or t_dt > end_at:
                        continue
                    if valid_from is None or t_dt < valid_from:
                        valid_from = t_dt
                    if valid_to is None or t_dt > valid_to:
                        valid_to = t_dt
                    vis_raw = _val("visibility", i)
                    all_points.append({
                        "address_id": address.id,
                        "valid_at": t_dt.isoformat(),
                        "temperature": _val("temperature_2m", i),
                        "precipitation_mm": _val("precipitation", i),
                        "precipitation_type": _wmo_to_precip_type(_val("weather_code", i)),
                        "cloud_cover": _val("cloud_cover", i),
                        "wind_speed_kmh": _val("wind_speed_10m", i),
                        "wind_direction_deg": _val("wind_direction_10m", i),
                        "visibility_km": round(vis_raw / 1000, 2) if vis_raw is not None else None,
                    })
            except Exception as exc:
                log.warning("Open-Meteo forecast failed for address %s: %s", address.id, exc)

        return {
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_to": valid_to.isoformat() if valid_to else None,
            "resolution_hours": self.forecast_resolution_hours,
            "points": all_points,
        }
