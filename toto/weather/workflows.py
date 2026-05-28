"""
Slugs and seeding logic for weather workflows.

These workflows are seeded by the `seed_weather_workflows` management command.
External callers trigger them via:
    from toto.workflows.api import trigger_workflow
    trigger_workflow(WEATHER_CURRENT_SLUG, {"location_ids": [...], "provider": "open_meteo"})
"""

WEATHER_CURRENT_SLUG = "weather-current"
WEATHER_FORECAST_SLUG = "weather-forecast"


WEATHER_CURRENT_LAMBDA = '''
import json
from toto.weather.providers import get_provider
from toto.weather.models import WeatherObservation
from toto.locations.models import Address
from django.utils import timezone

try:
    from dateutil.parser import parse as dt_parse
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False

_data = _input.get("data") or _input
location_ids = _data.get("location_ids", [])
provider_slug = _data.get("provider", "open_meteo")

addresses = list(Address.objects.filter(id__in=location_ids, geometry__isnull=False))
provider = get_provider(provider_slug)
results = provider.fetch_current(addresses)

saved = 0
errors = []
for r in results:
    if r.get("error"):
        errors.append({"address_id": r["address_id"], "error": r["error"]})
        continue
    obs_raw = r.get("observed_at")
    if obs_raw and _HAS_DATEUTIL:
        observed_at = dt_parse(obs_raw)
        if observed_at.tzinfo is None:
            from django.utils.timezone import make_aware
            observed_at = make_aware(observed_at)
    else:
        observed_at = timezone.now()
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

print(json.dumps({"data": {"success": True, "saved": saved, "errors": errors}}))
'''.strip()


WEATHER_FORECAST_LAMBDA = '''
import json
from toto.weather.providers import get_provider
from toto.weather.models import ForecastSession, ForecastPoint
from toto.locations.models import Address
from django.utils import timezone

try:
    from dateutil.parser import parse as dt_parse
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False

def _parse_dt(raw):
    if not raw:
        return timezone.now()
    if _HAS_DATEUTIL:
        dt = dt_parse(raw)
        if dt.tzinfo is None:
            from django.utils.timezone import make_aware
            dt = make_aware(dt)
        return dt
    return timezone.now()

_data = _input.get("data") or _input
location_ids = _data.get("location_ids", [])
provider_slug = _data.get("provider", "open_meteo")

addresses = list(Address.objects.filter(id__in=location_ids, geometry__isnull=False))
provider = get_provider(provider_slug)

start_at = _parse_dt(_data.get("start_at")) if _data.get("start_at") else None
end_at = _parse_dt(_data.get("end_at")) if _data.get("end_at") else None
result = provider.fetch_forecast(addresses, start_at=start_at, end_at=end_at)

valid_from = _parse_dt(result.get("valid_from"))
valid_to = _parse_dt(result.get("valid_to"))

session = ForecastSession.objects.create(
    provider=provider_slug,
    valid_from=valid_from,
    valid_to=valid_to,
    resolution_hours=result.get("resolution_hours", 1),
)

points = result.get("points", [])
objs = []
for p in points:
    if not p.get("address_id") or not p.get("valid_at"):
        continue
    objs.append(ForecastPoint(
        session=session,
        address_id=p["address_id"],
        valid_at=_parse_dt(p["valid_at"]),
        temperature=p.get("temperature"),
        precipitation_mm=p.get("precipitation_mm"),
        precipitation_type=p.get("precipitation_type", ""),
        cloud_cover=p.get("cloud_cover"),
        wind_speed_kmh=p.get("wind_speed_kmh"),
        wind_direction_deg=p.get("wind_direction_deg"),
        visibility_km=p.get("visibility_km"),
    ))

ForecastPoint.objects.bulk_create(objs, batch_size=500)

print(json.dumps({"data": {"success": True, "session_id": session.id, "points_saved": len(objs)}}))
'''.strip()
