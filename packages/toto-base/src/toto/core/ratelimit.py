"""A fixed-window rate limiter over Django's cache.

The platform had none (core/middleware.py records why the old one went, and
technology.md lists rate limits as an open risk). This is the smallest honest
one: a counter per key per window, in the shared cache (Redis on a deployed
stack, so web and workers count together), incremented atomically.

    hit("forum:pw:12:7", limit=5, window=300)   -> Hit(allowed, remaining, retry_after)
    check(...)                                   -> raises RateLimited when over

The bucket is part of the key (``rl:<key>:<window-number>``), so a TTL race
can never stretch a window: a new window is a new counter.

Two more shapes, for the sign-in lockout (``toto.core.signin_lockout``,
2026-09-30), which has to remember a run of failures rather than a rate:

    count("k", window=900)          -> 7     one more; forgotten `window` s after the LAST one
    peek("k")                       -> 7     the count, without counting
    hold("k:lock", seconds=900)     -> True  a deadline, set; False if one was already there
    held_until("k:lock")            -> 1790000000.0, or 0.0 once it has passed
    forget("k", "k:lock")                    gone

A fixed window would let a guesser spend nine tries at the end of one window
and nine more at the start of the next; a count that lives on while failures
keep coming has no such seam.

**Fail open.** When the cache is unavailable (django-redis with
IGNORE_EXCEPTIONS answers None) the call is allowed and a warning is logged:
refusing everything because Redis blinked is an outage, and the costly
operations this guards carry their own cost (the Argon2 derivation) and
nginx's `limit_req` as a backstop.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from django.core.cache import cache

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hit:
    allowed: bool
    remaining: int
    retry_after: int


class RateLimited(Exception):
    status_code = 429

    def __init__(self, retry_after: int, message: str = ""):
        self.retry_after = max(1, int(retry_after))
        super().__init__(message or f"Too many attempts. Try again in {self.retry_after} s.")


def _bucket(window: int, now: float) -> int:
    return int(now // window)


def hit(key: str, *, limit: int, window: int, now: float | None = None) -> Hit:
    """Count one attempt against ``key``; allowed while the count <= ``limit``."""
    now = time.time() if now is None else now
    bucket = _bucket(window, now)
    cache_key = f"rl:{key}:{bucket}"
    retry_after = int((bucket + 1) * window - now) or 1
    try:
        cache.add(cache_key, 0, timeout=window + 1)
        count = cache.incr(cache_key)
    except Exception:  # noqa: BLE001 — a cache outage is not a refusal
        log.warning("rate limiter: cache unavailable, allowing %s", key)
        return Hit(True, limit, 0)
    if count is None:
        log.warning("rate limiter: cache returned nothing, allowing %s", key)
        return Hit(True, limit, 0)
    return Hit(count <= limit, max(0, limit - count), retry_after)


def check(key: str, *, limit: int, window: int, message: str = "") -> Hit:
    result = hit(key, limit=limit, window=window)
    if not result.allowed:
        raise RateLimited(result.retry_after, message)
    return result


def reset(key: str, *, window: int, now: float | None = None) -> None:
    now = time.time() if now is None else now
    try:
        cache.delete(f"rl:{key}:{_bucket(window, now)}")
    except Exception:  # noqa: BLE001
        pass


# --- counts and holds (the sign-in lockout, 2026-09-30) ----------------------
# The same policy as `hit`: a cache that cannot answer is logged and treated
# as "nothing counted, nothing held", never as a refusal.


def count(key: str, *, window: int) -> int | None:
    """Count one more against ``key``; the count is forgotten ``window``
    seconds after its latest increment. None when the cache cannot say."""
    cache_key = f"rl:n:{key}"
    window = max(1, int(window))
    try:
        cache.add(cache_key, 0, timeout=window)
        value = cache.incr(cache_key)
        # Every failure pushes the forgetting back: the count spans a run.
        cache.touch(cache_key, window)
    except Exception:  # noqa: BLE001 - a cache outage is not a refusal
        log.warning("rate limiter: cache unavailable, not counting %s", key)
        return None
    if value is None:
        log.warning("rate limiter: cache returned nothing, not counting %s", key)
        return None
    return int(value)


def peek(key: str) -> int:
    try:
        return int(cache.get(f"rl:n:{key}") or 0)
    except Exception:  # noqa: BLE001
        return 0


def hold(key: str, *, seconds: float, replace: bool = False, now: float | None = None) -> bool:
    """Set a deadline ``seconds`` from now under ``key``.

    ``replace=False`` sets it only when none is there yet, and says whether
    this call did — so of two requests that cross a threshold together,
    exactly one learns it was first.
    """
    now = time.time() if now is None else now
    deadline = now + max(0.0, float(seconds))
    timeout = int(seconds) + 2
    cache_key = f"rl:h:{key}"
    try:
        if replace or cache.add(cache_key, deadline, timeout=timeout):
            if replace:
                cache.set(cache_key, deadline, timeout=timeout)
            return True
        # The entry outlives its deadline by the timeout's margin: a hold
        # whose deadline has passed is free to take.
        if float(cache.get(cache_key) or 0.0) <= now:
            cache.set(cache_key, deadline, timeout=timeout)
            return True
        return False
    except Exception:  # noqa: BLE001
        log.warning("rate limiter: cache unavailable, not holding %s", key)
        return False


def held_until(key: str, *, now: float | None = None) -> float:
    """The deadline under ``key`` while it is still ahead, else 0.0."""
    now = time.time() if now is None else now
    try:
        deadline = float(cache.get(f"rl:h:{key}") or 0.0)
    except Exception:  # noqa: BLE001
        log.warning("rate limiter: cache unavailable, %s reads as not held", key)
        return 0.0
    return deadline if deadline > now else 0.0


def forget(*keys: str) -> None:
    """Drop counts and holds alike; quiet when the cache is down."""
    names = [f"rl:{kind}:{key}" for key in keys for kind in ("n", "h")]
    try:
        cache.delete_many(names)
    except Exception:  # noqa: BLE001
        pass
