"""Who is actually connected to a channel right now.

The ``room_participants`` frame used to be built from every active ``ForumMember``, so the
UI showed the whole roster as permanently online. Real presence needs state shared across
worker processes, which the roster query cannot provide.

This keeps a small ``{display_name: last_seen_epoch}`` map per channel in the Django cache
(Redis in deployment, per-process locmem in dev). Presence is soft state: a lost read-modify-
write only drops someone until the next connect or disconnect broadcast re-publishes the map,
and entries older than :data:`STALE_AFTER_SECONDS` are ignored so a killed worker cannot
strand a ghost.
"""
import time

from django.core.cache import cache

# An entry not refreshed within this window is treated as gone.
STALE_AFTER_SECONDS = 90
# Cache TTL; comfortably longer than the staleness window.
_TTL = 600


def _key(channel_slug):
    return f"forum:presence:{channel_slug}"


def _load(channel_slug):
    try:
        return dict(cache.get(_key(channel_slug)) or {})
    except Exception:
        return {}


def _store(channel_slug, entries):
    try:
        cache.set(_key(channel_slug), entries, _TTL)
    except Exception:
        pass


def _fresh(entries, now=None):
    now = now or time.time()
    return {name: ts for name, ts in entries.items() if now - ts < STALE_AFTER_SECONDS}


def arrive(channel_slug, display_name):
    """Record that ``display_name`` has a live socket on this channel."""
    if not display_name:
        return
    entries = _fresh(_load(channel_slug))
    entries[display_name] = time.time()
    _store(channel_slug, entries)


def depart(channel_slug, display_name):
    """Record that ``display_name``'s socket closed."""
    if not display_name:
        return
    entries = _fresh(_load(channel_slug))
    entries.pop(display_name, None)
    _store(channel_slug, entries)


def online(channel_slug):
    """Display names currently connected to this channel."""
    return sorted(_fresh(_load(channel_slug)))
