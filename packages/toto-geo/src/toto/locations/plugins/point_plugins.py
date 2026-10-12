"""Named points another app keeps, offered as a route's ends (2026-10-12).

A saved address is one kind of place a route starts or ends at; a host app
may track others — the objects of a world model, each with its coordinates
and a type — that are not addresses and should not be copied into the
address book (they move; their own app is their record). A source of such
points registers here, under a prefix, in its ``ready()``:

    LocationPointPlugin.register("wotan", options=objects_for, resolve=object_for)

``options(user)`` answers the points this viewer may pick, each
``{"key", "label", "group", "lat", "lng"}``; ``resolve(user, key)`` answers
one of them, ``{"label", "lat", "lng"}``, or None when it is unknown or
hidden from this viewer. The route search lists them beside the saved
addresses, grouped, with the value ``"<prefix>:<key>"``, and resolves a
picked one on the server through ``resolve`` — never from the browser's
coordinates — so a point the viewer may not see is "not available" exactly
like a hidden address. A source that fails answers nothing: the route search
is never refused because a point source is down.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)

SEPARATOR = ":"
#: "temporary:" is the route search's own marker for an unsaved address.
RESERVED = {"temporary"}


def _coordinate(value, bound):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and -bound <= number <= bound else None


def _point(raw: dict, *, key=None) -> dict | None:
    """A checked point, or None: coordinates on the earth, a label."""
    if not isinstance(raw, dict):
        return None
    lat, lng = _coordinate(raw.get("lat"), 90), _coordinate(raw.get("lng"), 180)
    if lat is None or lng is None:
        return None
    point = {"label": str(raw.get("label") or key or "")[:200], "lat": lat, "lng": lng}
    if key is not None:
        point["key"] = str(key)
        point["group"] = str(raw.get("group") or "")[:80]
    return point


class LocationPointPlugin:
    _sources: dict = {}

    @classmethod
    def register(cls, prefix: str, *, options, resolve):
        if not prefix or SEPARATOR in prefix or prefix in RESERVED or not prefix.isidentifier():
            raise ValueError(f"a point prefix is a plain name, not {prefix!r}")
        cls._sources[prefix] = (options, resolve)

    @classmethod
    def unregister(cls, prefix: str):
        cls._sources.pop(prefix, None)

    @classmethod
    def split(cls, value) -> tuple[str, str] | None:
        """(prefix, key) when ``value`` names a registered source's point."""
        prefix, separator, key = str(value or "").partition(SEPARATOR)
        if not separator or not key or prefix not in cls._sources:
            return None
        return prefix, key

    @classmethod
    def is_point(cls, value) -> bool:
        return cls.split(value) is not None

    @classmethod
    def resolve(cls, user, value) -> dict | None:
        """``{"id", "label", "lat", "lng"}`` for a picked point, or None."""
        parts = cls.split(value)
        if parts is None:
            return None
        prefix, key = parts
        try:
            point = _point(cls._sources[prefix][1](user, key))
        except Exception as exc:  # noqa: BLE001 — a source that fails answers nothing
            log.warning("locations: point source %s failed: %s: %s", prefix, type(exc).__name__, exc)
            return None
        if point is None:
            return None
        return {"id": f"{prefix}{SEPARATOR}{key}", **point}

    @classmethod
    def options(cls, user) -> list[dict]:
        """Every source's points for this viewer, as groups:
        ``[{"label": group, "options": [{"id", "label", "lat", "lng"}]}]``,
        groups in the order the sources answer them."""
        groups: dict = {}
        for prefix, (options, _resolve) in cls._sources.items():
            try:
                rows = list(options(user) or [])
            except Exception as exc:  # noqa: BLE001
                log.warning("locations: point source %s failed: %s: %s", prefix, type(exc).__name__, exc)
                continue
            for raw in rows:
                key = raw.get("key") if isinstance(raw, dict) else None
                if not key:
                    continue
                point = _point(raw, key=key)
                if point is None:
                    continue
                group = point.pop("group") or prefix
                groups.setdefault(group, []).append(
                    {"id": f"{prefix}{SEPARATOR}{point.pop('key')}", **point})
        return [{"label": label, "options": rows} for label, rows in groups.items()]
