import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


DEFAULT_GEOCODING_SETTINGS = {
    "enabled": True,
    "reverse_url": "https://nominatim.openstreetmap.org/reverse",
    "user_agent": "toto-locations/1.0",
    "timeout": 8,
    "accept_language": "en",
    "fail_silently": True,
}


def geocoding_settings():
    config = DEFAULT_GEOCODING_SETTINGS.copy()
    config.update(getattr(settings, "LOCATIONS_GEOCODING", {}) or {})
    return config


def first_present(*values, default=""):
    for value in values:
        if value not in (None, ""):
            return value

    return default


def geocoding_enabled(config):
    return bool(config.get("enabled"))


def geocoding_headers(config):
    headers = {
        "User-Agent": config.get("user_agent") or DEFAULT_GEOCODING_SETTINGS["user_agent"],
    }

    accept_language = config.get("accept_language")
    if accept_language:
        headers["Accept-Language"] = accept_language

    return headers


def build_reverse_geocode_url(latitude, longitude, config):
    query = urlencode({
        "format": "jsonv2",
        "lat": latitude,
        "lon": longitude,
        "addressdetails": 1,
        "zoom": 18,
    })

    return f"{config['reverse_url']}?{query}"


def fetch_reverse_geocode_payload(latitude, longitude, config):
    url = build_reverse_geocode_url(latitude, longitude, config)
    request = Request(url, headers=geocoding_headers(config))

    with urlopen(request, timeout=config.get("timeout", 8)) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_reverse_geocode_payload(payload):
    address = payload.get("address") or {}

    return {
        "country_name": first_present(
            (address.get("country_code") or "").upper(),
        ),
        "state_or_province_name": first_present(
            address.get("state"),
            address.get("province"),
            address.get("region"),
            address.get("county"),
        ),
        "locality_name": first_present(
            address.get("city"),
            address.get("town"),
            address.get("village"),
            address.get("municipality"),
            address.get("hamlet"),
            address.get("suburb"),
        ),
        "street": first_present(
            address.get("road"),
            address.get("pedestrian"),
            address.get("footway"),
            address.get("path"),
            address.get("cycleway"),
            payload.get("name"),
            address.get("amenity"),
            address.get("tourism"),
            address.get("historic"),
        ),
        "building": first_present(
            address.get("house_number"),
            address.get("building"),
            address.get("amenity"),
            address.get("tourism"),
            address.get("historic"),
            payload.get("name"),
            default="Map point",
        ),
    }


def reverse_geocode_address(latitude, longitude):
    """
    Reverse-geocode WGS84 latitude/longitude into AddressCreateForm initial data.

    Returns:
        dict: {
            "country_name": "...",
            "state_or_province_name": "...",
            "locality_name": "...",
            "street": "...",
            "building": "...",
        }

    Returns {} when coordinates are missing, geocoding is disabled, or the
    provider fails and fail_silently is enabled.
    """
    if latitude in (None, "") or longitude in (None, ""):
        return {}

    config = geocoding_settings()

    if not geocoding_enabled(config):
        return {}

    try:
        payload = fetch_reverse_geocode_payload(latitude, longitude, config)
        return normalize_reverse_geocode_payload(payload)

    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
        if config.get("fail_silently", True):
            return {}

        raise