"""A fixed-window rate limiter over Django's cache.

The platform had none (core/middleware.py records why the old one went, and
technology.md lists rate limits as an open risk). This is the smallest honest
one: a counter per key per window, in the shared cache (Redis on a deployed
stack, so web and workers count together), incremented atomically.

    hit("forum:pw:12:7", limit=5, window=300)   -> Hit(allowed, remaining, retry_after)
    check(...)                                   -> raises RateLimited when over

The bucket is part of the key (``rl:<key>:<window-number>``), so a TTL race
can never stretch a window: a new window is a new counter.

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
