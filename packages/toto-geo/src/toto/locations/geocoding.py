"""Geocoding on the server, charged per lookup (2026-09-28).

Two questions a member can put to the map: "where is Gdańsk?" (search) and
"what is at this pin?" (reverse). Both go to the host's provider
(`LOCATIONS_GEOCODING`, Nominatim by default) through this module, because
each answer costs one `locations.geocode` lookup — compute mana, quoted by
`{% price_hint "locations.geocode" %}` beside the control that spends it.
Nothing here runs as-you-type: the page asks on Enter or a button.

Every call walks the same doors, cheapest refusal first::

    signed in -> geocoding on here -> input valid -> cache (24 h)
    -> per-user limit (20 a minute) -> quota and funds
    -> provider, on a cache miss only, behind one throttle for everybody
    -> record one usage event and charge one lookup

WHAT IS CHARGED. Every lookup that returns an answer, a cache hit included:
the price is per lookup and is shown on the button, so what a member pays
cannot depend on whether somebody else happened to ask first. An empty search
and a point with no address are answers. Nothing is charged for bad input, a
host with geocoding off, a throttle, a billing refusal or a provider failure.

THE PROVIDER THROTTLE is one `toto.core.ratelimit` key shared by all users,
one call a second — Nominatim's usage policy, and the reason a busy map cannot
get the host's address banned. A call that finds the second taken waits for
the next one, up to `PROVIDER_WAIT`, before refusing with 429: a route search
resolves two names back to back, and refusing the second would leave the
member charged for half a route.

PRIVACY. Charge descriptions and usage events name the kind of lookup ("Place
search", "Address lookup"), never the text or the point, and cache keys are
hashes: the ledger, the usage tables and Redis are read by people who have no
business knowing where a member looked.

The billing refusals (QuotaExceeded 429, InArrears 402, InsufficientFunds 402)
propagate as they are; a caller catches ``REFUSALS`` (them and GeocodingError)
and answers with ``exc.status_code`` and ``str(exc)``. No GIS imports:
socialhub and the host's Places app call this on any build.
"""

from __future__ import annotations

import hashlib
import logging
import math
import time
from typing import NamedTuple

from django.core.cache import cache
from django.utils.translation import gettext as _

from toto.core import ratelimit
from toto.quota.api import InArrears, QuotaExceeded
from toto.quota.charge import InsufficientFunds

from . import billing
from .geocode import (
    GeocodingUnavailable,
    describe_point,
    geocoding_enabled,
    geocoding_settings,
    search_places,
)

logger = logging.getLogger(__name__)

QUERY_MIN = 2
QUERY_MAX = 200
#: Five decimals is about a metre: one cache entry per doorstep, not per pixel.
POINT_DECIMALS = 5
CACHE_SECONDS = 24 * 60 * 60

USER_LIMIT = 20
USER_WINDOW = 60
PROVIDER_KEY = "locations:geocode:provider"
PROVIDER_LIMIT = 1
PROVIDER_WINDOW = 1
#: How long a call may wait for the provider's next free second (seconds).
PROVIDER_WAIT = 2.0

#: What the ledger and the usage event say — the kind of lookup, nothing more.
SEARCH_LABEL = "Place search"
REVERSE_LABEL = "Address lookup"

_MISS = object()


class GeocodingError(Exception):
    """A lookup refused before or instead of an answer. Never charged.

    ``status_code``: 400 bad input, 403 not signed in, 404 geocoding off on
    this host, 429 throttled (``retry_after`` set), 503 provider unavailable.
    ``str(exc)`` is a sentence for the member.
    """

    def __init__(self, message, status_code, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


#: Everything a lookup can be refused with, for one ``except`` at a door. Each
#: carries ``status_code`` and reads as a sentence.
REFUSALS = (GeocodingError, QuotaExceeded, InArrears, InsufficientFunds)


class Answer(NamedTuple):
    """A charged lookup's result, and whether the cache supplied it."""

    value: object
    cached: bool


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def search(user, query) -> list[dict]:
    """Places matching ``query``: [{"label", "name", "lat", "lng", "type",
    "country_name", "state_or_province_name", "locality_name"}], lat/lng
    floats. Charged as one lookup."""
    return search_answer(user, query).value


def reverse(user, lat, lng) -> dict:
    """The address at a point: {"lat", "lng", "label", "fields": {...},
    "found"}. Charged as one lookup, found or not."""
    return reverse_answer(user, lat, lng).value


def first_match(user, query) -> dict | None:
    """The best match for a typed place name, or None. One lookup — the
    search behind it — whatever it finds."""
    results = search(user, query)
    return results[0] if results else None


def clean_query(query) -> str:
    """``query`` with its whitespace folded, or GeocodingError 400 when it is
    too short or too long for a search. Route search checks every typed name
    with it before the first is looked up, so a bad second name cannot be
    refused after the first was charged."""
    text = " ".join(str(query or "").split())
    if len(text) < QUERY_MIN:
        raise GeocodingError(
            _("Type at least %(n)d characters to search.") % {"n": QUERY_MIN}, 400)
    if len(text) > QUERY_MAX:
        raise GeocodingError(
            _("Search for at most %(n)d characters.") % {"n": QUERY_MAX}, 400)
    return text


def check_affordable(user, n=1) -> None:
    """Raise the billing refusal ``n`` lookups would meet, before any of them.

    Route search asks for two before resolving "from" and "to", so the second
    name cannot be refused for money after the first was charged."""
    _require_member(user)
    billing.check_affordable(user, n)


def search_answer(user, query) -> Answer:
    """`search`, with whether the answer came from the cache (the endpoints
    report it)."""
    _require_member(user)
    config = _config()
    text = clean_query(query)
    key = _cache_key("search", config, text.casefold())
    return _lookup(user, key, SEARCH_LABEL, lambda: search_places(text, config))


def reverse_answer(user, lat, lng) -> Answer:
    """`reverse`, with whether the answer came from the cache."""
    _require_member(user)
    config = _config()
    lat, lng = _clean_point(lat, lng)
    key = _cache_key("reverse", config, f"{lat:.{POINT_DECIMALS}f},{lng:.{POINT_DECIMALS}f}")

    def ask():
        return {"lat": lat, "lng": lng, **describe_point(lat, lng, config)}

    return _lookup(user, key, REVERSE_LABEL, ask)


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------

def _require_member(user):
    if user is None or not getattr(user, "is_authenticated", False):
        raise GeocodingError(_("Sign in to look places up."), 403)


def _config():
    config = geocoding_settings()
    if not geocoding_enabled(config):
        raise GeocodingError(_("Place lookup is not available on this server."), 404)
    return config


def _clean_point(lat, lng):
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        raise GeocodingError(_("Latitude and longitude must be numbers."), 400) from None
    if not (math.isfinite(lat) and math.isfinite(lng)
            and -90 <= lat <= 90 and -180 <= lng <= 180):
        raise GeocodingError(_("That point is not on the globe."), 400)
    return round(lat, POINT_DECIMALS), round(lng, POINT_DECIMALS)


def _cache_key(kind, config, subject):
    # The language is part of the answer (labels are localised); the provider
    # URL is part of it too, so a host that moves provider starts afresh.
    material = "|".join((kind, config.get("search_url", ""), config.get("reverse_url", ""),
                         str(config.get("accept_language", "")),
                         str(config.get("search_limit", "")), subject))
    return f"locations:geocode:{kind}:{hashlib.sha256(material.encode()).hexdigest()}"


def _cache_get(key):
    try:
        return cache.get(key, _MISS)
    except Exception:  # noqa: BLE001 - a cache outage is a miss, not a refusal
        logger.warning("geocoding: cache unavailable, asking the provider")
        return _MISS


def _cache_set(key, value):
    try:
        cache.set(key, value, CACHE_SECONDS)
    except Exception:  # noqa: BLE001
        logger.warning("geocoding: cache unavailable, answer not kept")


def _throttle_member(user):
    hit = ratelimit.hit(f"locations:geocode:user:{user.pk}",
                        limit=USER_LIMIT, window=USER_WINDOW)
    if not hit.allowed:
        raise GeocodingError(
            _("Too many place lookups. Try again in %(s)d s.") % {"s": hit.retry_after},
            429, retry_after=hit.retry_after)


def _wait_for_provider():
    """Take the provider's one call this second, waiting briefly for it."""
    deadline = time.monotonic() + PROVIDER_WAIT
    while True:
        if ratelimit.hit(PROVIDER_KEY, limit=PROVIDER_LIMIT, window=PROVIDER_WINDOW).allowed:
            return
        # The rate limiter's retry_after is whole seconds; the next window
        # here opens within one, so wait exactly until it does.
        pause = PROVIDER_WINDOW - (time.time() % PROVIDER_WINDOW) + 0.01
        if time.monotonic() + pause > deadline:
            raise GeocodingError(
                _("Place lookup is busy. Try again in a moment."), 429, retry_after=1)
        time.sleep(pause)


def _lookup(user, key, label, ask) -> Answer:
    cached = _cache_get(key)
    _throttle_member(user)
    billing.check_affordable(user, 1)

    if cached is _MISS:
        _wait_for_provider()
        try:
            value = ask()
        except GeocodingUnavailable:
            # "This lookup": a route search may have paid for its first
            # name a moment ago, in the same request.
            raise GeocodingError(
                _("Place lookup is not answering. Try again later; "
                  "this lookup was not charged."), 503) from None
        _cache_set(key, value)
    else:
        value = cached

    billing.settle_lookup(user, label)
    return Answer(value, cached is not _MISS)
