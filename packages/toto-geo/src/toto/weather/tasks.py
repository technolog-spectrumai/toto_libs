"""
Celery tasks for automatic weather fetching.

The beat schedule in portal/settings.py triggers `auto_refresh_weather`
every N minutes. It fetches current conditions for every Address that has a
geometry (i.e. all geocoded locations), using the provider configured in
WeatherSettings (defaults to open_meteo).
"""
from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("weather.tasks")


@shared_task(bind=True, name="toto.weather.tasks.auto_refresh_weather", max_retries=2)
def auto_refresh_weather(self):
    """
    Fetch current weather for all geocoded locations and store WeatherObservation rows.

    Triggered by Celery beat. Uses the provider set in WeatherSettings.
    Falls back to open_meteo if no setting exists.
    """
    try:
        from toto.locations.models import Address
        from toto.weather.models import WeatherSettings
        from toto.weather.providers import get_provider

        settings = WeatherSettings.get()
        provider_slug = getattr(settings, "provider", "open_meteo") or "open_meteo"

        addresses = list(Address.objects.filter(geometry__isnull=False))
        if not addresses:
            logger.info("auto_refresh_weather: no geocoded addresses — skipping.")
            return {"fetched": 0, "errors": 0}

        provider = get_provider(provider_slug)
        results = provider.fetch_current(addresses)

        from django.utils import timezone
        saved = 0
        errors = []
        for r in results:
            if r.get("error"):
                errors.append(r)
                continue
            try:
                from toto.weather.models import WeatherObservation
                observed_at = r.get("observed_at") or timezone.now()
                if isinstance(observed_at, str):
                    from django.utils.dateparse import parse_datetime
                    observed_at = parse_datetime(observed_at) or timezone.now()

                WeatherObservation.objects.create(
                    address_id=r["address_id"],
                    provider=provider_slug,
                    observed_at=observed_at,
                    temperature=r.get("temperature"),
                    precipitation_mm=r.get("precipitation_mm"),
                    precipitation_type=r.get("precipitation_type", ""),
                    cloud_cover=r.get("cloud_cover"),
                    wind_speed_kmh=r.get("wind_speed_kmh"),
                    wind_direction_deg=r.get("wind_direction_deg"),
                    visibility_km=r.get("visibility_km"),
                )
                saved += 1
            except Exception as exc:
                logger.warning("auto_refresh_weather: failed to save observation: %s", exc)
                errors.append({"address_id": r.get("address_id"), "error": str(exc)})

        logger.info(
            "auto_refresh_weather: provider=%s saved=%d errors=%d",
            provider_slug, saved, len(errors),
        )
        return {"fetched": saved, "errors": len(errors)}

    except Exception as exc:
        logger.exception("auto_refresh_weather failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)
