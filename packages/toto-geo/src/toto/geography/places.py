"""Place-name search on the server, charged once per answered search.

"Where is Warsaw?" goes to the host's provider (``LOCATIONS_GEOCODING``,
Nominatim by default) through this module. Nothing here runs as-you-type:
the page asks on Enter or the button, and each press carries its own ``op``
(``charging``).

Every search walks the same doors, cheapest refusal first::

    signed in -> search on here -> query and op valid
    -> per-member limit (20 a minute)
    -> known op: a replay, or 409
    -> fresh op: quota and funds (402 before anybody is asked)
    -> answer cache (one hour) -> provider, on a miss only, behind one
       throttle for everybody
    -> an empty answer is given, uncharged
    -> one usage event and one charge; a ledger refusal withholds the answer

WHAT IS CHARGED. Every fresh search that finds something, a cache hit
included: the price is on the button, so what a member pays cannot depend on
whether somebody else asked first. A replay of the same op is not charged
again. Nothing is charged for bad input, a throttle, a refusal, a provider
failure or an empty answer.

THE ANSWER CACHE keeps the provider's answer for
``GEOGRAPHY_GEOCODE_CACHE_SECONDS`` (3600; 0 turns it off; Nominatim's usage
policy asks clients to cache). The key is a keyed hash of the normalised
query, so nobody who reads the cache can tell what was asked; the value is
the provider's answer and names no member.

THE PROVIDER THROTTLE is one ``toto.core.ratelimit`` key shared by all
members, one call a second: Nominatim's usage policy. It lives in the cache
and does not hold while the cache is down.

PRIVACY. The charge's description and the usage event say "Place search",
never the text; the audit record says the same.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings
from django.core.cache import cache
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext as _

from toto.core import ratelimit
from toto.quota.api import InArrears, QuotaExceeded
from toto.quota.charge import InsufficientFunds

from . import audit, charging, metrics
from .charging import Refusal
from .geocode import (
    GeocodingUnavailable,
    geocoding_enabled,
    geocoding_settings,
    search_places,
)

logger = logging.getLogger(__name__)

QUERY_MIN = 2
QUERY_MAX = 200

USER_LIMIT = 20
USER_WINDOW = 60
PROVIDER_KEY = "geography:places:provider"
PROVIDER_LIMIT = 1
PROVIDER_WINDOW = 1
#: How long a call may wait for the provider's next free second (seconds).
PROVIDER_WAIT = 2.0

#: What the ledger and the usage event say: the kind of action, nothing more.
SEARCH_LABEL = "Place search"

#: Everything a charged door can be refused with, for one ``except``. Each
#: carries ``status_code`` and reads as a sentence.
REFUSALS = (Refusal, QuotaExceeded, InArrears, InsufficientFunds)

_MISS = object()
_SALT = "toto.geography.places"


def cache_seconds() -> int:
    return int(getattr(settings, "GEOGRAPHY_GEOCODE_CACHE_SECONDS", 3600) or 0)


def require_member(user):
    if user is None or not getattr(user, "is_authenticated", False):
        raise Refusal(_("Sign in to use the map."), 403)


def clean_query(query) -> str:
    """``query`` with its whitespace folded, or 400."""
    if not isinstance(query, str):
        raise Refusal(_("Type at least %(n)d characters to search.") % {"n": QUERY_MIN}, 400)
    text = " ".join(query.split())
    if len(text) < QUERY_MIN:
        raise Refusal(_("Type at least %(n)d characters to search.") % {"n": QUERY_MIN}, 400)
    if len(text) > QUERY_MAX:
        raise Refusal(_("Search for at most %(n)d characters.") % {"n": QUERY_MAX}, 400)
    return text


def _config():
    config = geocoding_settings()
    if not geocoding_enabled(config):
        raise Refusal(_("Place search is not available on this server."), 404)
    return config


def enabled() -> bool:
    return geocoding_enabled(geocoding_settings())


def _cache_key(config, subject) -> str:
    # The language and the provider are part of the answer; the query is in
    # the key only as a keyed hash.
    material = "|".join((config.get("search_url", ""), str(config.get("accept_language", "")),
                         str(config.get("search_limit", "")), subject))
    return "geography:places:" + salted_hmac(_SALT, material, algorithm="sha256").hexdigest()


def _cache_get(key):
    if not cache_seconds():
        return _MISS
    try:
        return cache.get(key, _MISS)
    except Exception:  # noqa: BLE001 - a cache outage is a miss, not a refusal
        logger.warning("places: cache unavailable, asking the provider")
        return _MISS


def _cache_set(key, value):
    seconds = cache_seconds()
    if not seconds:
        return
    try:
        cache.set(key, value, seconds)
    except Exception:  # noqa: BLE001
        logger.warning("places: cache unavailable, answer not kept")


def throttle_member(user, kind, limit, window):
    hit = ratelimit.hit(f"geography:{kind}:user:{user.pk}", limit=limit, window=window)
    if not hit.allowed:
        raise Refusal(_("Too many searches. Try again in %(s)d s.") % {"s": hit.retry_after},
                      429, retry_after=hit.retry_after)


def wait_for_provider(key, *, limit=None, wait=None):
    """Take the provider's one call this second, waiting briefly for it."""
    limit = PROVIDER_LIMIT if limit is None else limit
    wait = PROVIDER_WAIT if wait is None else wait
    deadline = time.monotonic() + wait
    while True:
        if ratelimit.hit(key, limit=limit, window=PROVIDER_WINDOW).allowed:
            return
        pause = PROVIDER_WINDOW - (time.time() % PROVIDER_WINDOW) + 0.01
        if time.monotonic() + pause > deadline:
            raise Refusal(_("The map service is busy. Try again in a moment."), 429,
                          retry_after=1)
        time.sleep(pause)


def _trim(results) -> list[dict]:
    """What the page gets: a label and a point for each hit."""
    return [{"label": hit.get("label") or hit.get("name") or "",
             "lat": hit["lat"], "lng": hit["lng"]} for hit in results]


def search(user, query, op) -> tuple[list[dict], bool]:
    """``(results, charged)``: places matching ``query``, each
    ``{"label", "lat", "lng"}``, and whether this call was charged."""
    require_member(user)
    config = _config()
    text = clean_query(query)
    op = charging.clean_op(op)
    throttle_member(user, "places", USER_LIMIT, USER_WINDOW)

    body = {"q": text.casefold()}
    replay = charging.known(user, op, body)
    if not replay:
        charging.afford(user, metrics.LOOKUP)

    key = _cache_key(config, text.casefold())
    value = _cache_get(key)
    if value is _MISS:
        wait_for_provider(PROVIDER_KEY)
        try:
            value = search_places(text, config)
        except GeocodingUnavailable:
            raise Refusal(_("Place search is not answering. Try again later; "
                            "this search was not charged."), 503) from None
        _cache_set(key, value)

    results = _trim(value)
    if replay or not results:
        return results, False
    try:
        _nothing, charged = charging.settle(user, metrics.LOOKUP, op, body, SEARCH_LABEL)
    except charging.Duplicate:
        return results, False
    audit.record(audit.SEARCH, user, metric=metrics.LOOKUP,
                 amount=charging.amount_of(charged), outcome="charged")
    return results, True
