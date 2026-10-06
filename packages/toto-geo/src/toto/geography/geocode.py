import http.client
import json
import logging
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)


DEFAULT_GEOCODING_SETTINGS = {
    "enabled": True,
    "reverse_url": "https://nominatim.openstreetmap.org/reverse",
    "search_url": "https://nominatim.openstreetmap.org/search",
    "user_agent": "toto-locations/1.0",
    "timeout": 8,
    "accept_language": "en",
    "fail_silently": True,
    "search_limit": 5,
}


#: Everything a provider call can fail with (2026-09-28). URLError, HTTPError
#: and timeouts are all OSError; a connection dropped mid-answer is an
#: HTTPException; a body that is not the JSON asked for is a ValueError
#: (JSONDecodeError, UnicodeDecodeError, and the normalisers below refusing an
#: odd payload). The fail-silent helpers used to catch only HTTPError,
#: URLError, TimeoutError and JSONDecodeError, so a reset connection, a
#: truncated body (IncompleteRead) or a non-list answer was a 500.
PROVIDER_ERRORS = (OSError, http.client.HTTPException, ValueError)

#: The address fields a reverse lookup fills, in AddressCreateForm's names.
ADDRESS_FIELDS = (
    "country_name",
    "state_or_province_name",
    "locality_name",
    "street",
    "building",
)


class GeocodingUnavailable(Exception):
    """The provider could not be reached, or answered with something unusable.

    Raised only by the raising variants (`search_places`, `describe_point`),
    which the charged service in `toto.locations.geocoding` uses: it must tell
    a failure from an empty answer, because an answer is charged and a failure
    is not. The fail-silent helpers still answer [] / {} as before.
    """


def log_provider_failure(kind, exc):
    # The query and the point stay out of the log: where a member looked is
    # theirs. The exception's own text names the network or parse failure.
    logger.warning("geocoding: %s provider failed: %s: %s",
                   kind, type(exc).__name__, exc)


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


def normalize_reverse_geocode_payload(payload, building_default="Map point"):
    if not isinstance(payload, dict):
        raise ValueError("reverse geocoding answered with something other than an object")

    address = payload.get("address")
    if not isinstance(address, dict):
        address = {}

    return {
        "label": first_present(payload.get("display_name")),
        "country_name": first_present(
            str(address.get("country_code") or "").upper(),
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
            default=building_default,
        ),
    }


def reverse_geocode_address(latitude, longitude):
    """
    Reverse-geocode WGS84 latitude/longitude into AddressCreateForm initial data.

    Returns:
        dict: {
            "label": "...",           # the provider's display name
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

    except PROVIDER_ERRORS as exc:
        log_provider_failure("reverse", exc)
        if config.get("fail_silently", True):
            return {}

        raise


def describe_point(latitude, longitude, config=None):
    """Reverse geocoding that says when it failed (2026-09-28).

    For the charged service: raises GeocodingUnavailable (logged) where
    `reverse_geocode_address` answers {}. A point the provider has no address
    for (open sea, Nominatim's {"error": "Unable to geocode"}) is an answer,
    not a failure: found=False with blank fields.

    Returns:
        dict: {"label": "...", "fields": {ADDRESS_FIELDS...}, "found": bool}

    The fields carry no "Map point" placeholder: they fill a form, and a
    building number that reads "Map point" is not an address.
    """
    config = config or geocoding_settings()

    try:
        payload = fetch_reverse_geocode_payload(latitude, longitude, config)
        normalized = normalize_reverse_geocode_payload(payload, building_default="")

    except PROVIDER_ERRORS as exc:
        log_provider_failure("reverse", exc)
        raise GeocodingUnavailable("reverse") from exc

    if payload.get("error") or not payload.get("address"):
        return {"label": "", "fields": dict.fromkeys(ADDRESS_FIELDS, ""), "found": False}

    return {
        "label": normalized["label"],
        "fields": {key: str(normalized[key]) for key in ADDRESS_FIELDS},
        "found": True,
    }


# ---------------------------------------------------------------------
# Forward geocoding for map search, e.g. "poznan"
# ---------------------------------------------------------------------

def build_forward_geocode_url(query, config):
    params = urlencode({
        "format": "jsonv2",
        "q": query,
        "addressdetails": 1,
        "limit": config.get("search_limit", 5),
    })

    return f"{config['search_url']}?{params}"


def fetch_forward_geocode_payload(query, config):
    url = build_forward_geocode_url(query, config)
    request = Request(url, headers=geocoding_headers(config))

    with urlopen(request, timeout=config.get("timeout", 8)) as response:
        return json.loads(response.read().decode("utf-8"))


def coordinate_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def normalize_forward_geocode_item(item):
    address = item.get("address")
    if not isinstance(address, dict):
        address = {}

    return {
        "label": first_present(
            item.get("display_name"),
            item.get("name"),
            address.get("city"),
            address.get("town"),
            address.get("village"),
            default="Search result",
        ),
        "name": first_present(
            item.get("name"),
            address.get("city"),
            address.get("town"),
            address.get("village"),
            item.get("display_name"),
            default="Search result",
        ),
        "lat": coordinate_or_none(item.get("lat")),
        "lng": coordinate_or_none(item.get("lon")),
        "type": first_present(
            item.get("type"),
            item.get("class"),
            default="place",
        ),
        "country_name": first_present(
            str(address.get("country_code") or "").upper(),
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
    }


def normalize_forward_geocode_payload(payload):
    if not isinstance(payload, list):
        raise ValueError("forward geocoding answered with something other than a list")

    results = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        normalized = normalize_forward_geocode_item(item)

        if normalized["lat"] is None or normalized["lng"] is None:
            continue

        results.append(normalized)

    return results


def forward_geocode_locations(query):
    """
    Forward-geocode a place name like "poznan" into map search results.

    Returns:
        list[dict]: [
            {
                "label": "...",
                "name": "...",
                "lat": 52.40,           # floats since 2026-09-28
                "lng": 16.93,
                "type": "...",
                ...
            }
        ]
    """
    query = (query or "").strip()

    if not query:
        return []

    config = geocoding_settings()

    if not geocoding_enabled(config):
        return []

    try:
        payload = fetch_forward_geocode_payload(query, config)
        return normalize_forward_geocode_payload(payload)

    except PROVIDER_ERRORS as exc:
        log_provider_failure("search", exc)
        if config.get("fail_silently", True):
            return []

        raise


def search_places(query, config=None):
    """Forward geocoding that says when it failed (2026-09-28).

    For the charged service: raises GeocodingUnavailable (logged) where
    `forward_geocode_locations` answers []. An empty list here means the
    provider answered and found nothing — which is charged; a failure is not.
    The caller has already validated the query and checked `enabled`.
    """
    config = config or geocoding_settings()

    try:
        payload = fetch_forward_geocode_payload(query, config)
        return normalize_forward_geocode_payload(payload)

    except PROVIDER_ERRORS as exc:
        log_provider_failure("search", exc)
        raise GeocodingUnavailable("search") from exc