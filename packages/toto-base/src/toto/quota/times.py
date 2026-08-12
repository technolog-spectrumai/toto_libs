"""Time limits, declared once — and the door to the grants that raise them.

Some resources are bounded by a duration rather than a count: how long a
Python kernel may sit idle, how long one compile or media job may run. Those
bounds used to be constants scattered through the apps that enforce them.
This module is the registry half of making them *dials*: each app declares
its time limits in ``<app>/times.py`` (autodiscovered from
``QuotaConfig.ready()``, the same contract as ``metrics.py`` — pure data, no
models, no database), and every enforcement site reads the effective value
through the façade below.

The *grant* half — a user paying to hold a limit above its free default —
lives in ``toto.tax`` (toto-economy), which most hosts do not ship. So this
module follows :mod:`toto.quota.rates` exactly: the registry is importable
everywhere, and every façade function lazy-imports the economy inside its
body and degrades to the free default when it is absent. A host with no
economy simply has dials welded to their defaults, which is correct. Only
plain data crosses the boundary — no template ever holds a tax model.

Free defaults and ceilings are declaration constants, not staff knobs: each
ceiling is derived from infrastructure arithmetic (celery time limits, the
Redis visibility timeout) that a database knob could silently violate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

from django.db import DatabaseError

logger = logging.getLogger("toto.quota")


class DuplicateTimeLimit(Exception):
    """Two apps claimed the same time-limit key."""


@dataclass(frozen=True)
class TimeLimit:
    """One duration bound a user may (where the economy allows) raise.

    ``free_seconds`` is what everyone gets and what the consuming app used as
    its constant before the dial existed — declaring a limit must never change
    behavior for someone who touches nothing. ``ceiling_seconds`` is the hard
    cap no payment exceeds. ``scope`` is "user" for account-wide dials and
    "workspace" for per-object ones, in which case ``scope_model`` names the
    object's model as an "app_label.Model" string (resolved lazily — a
    declaration must not import another app) and ``scope_owner_attr`` says
    which attribute holds the billing owner's user id.
    """

    key: str
    label: str
    app_label: str
    scope: str = "user"                  # "user" | "workspace"
    free_seconds: int = 0
    ceiling_seconds: int = 0
    description: str = ""
    display_unit: str = "minutes"        # "minutes" | "hours" — UI hint only
    scope_model: str = ""                # e.g. "ambrosia.Workspace"
    scope_owner_attr: str = "owner_id"

    def __str__(self) -> str:
        return f"{self.key} — {self.label}"


class TimeLimitRegistry:
    """In-memory, populated at startup. Never touches the database."""

    def __init__(self) -> None:
        self._limits: dict[str, TimeLimit] = {}

    def register(self, limit: TimeLimit) -> TimeLimit:
        existing = self._limits.get(limit.key)
        if existing is not None and existing != limit:
            raise DuplicateTimeLimit(
                f"{limit.key!r} is already declared by {existing.app_label!r}; "
                f"{limit.app_label!r} cannot claim it too."
            )
        self._limits[limit.key] = limit
        return limit

    def get(self, key: str) -> TimeLimit | None:
        return self._limits.get(key)

    def all(self) -> list[TimeLimit]:
        return sorted(self._limits.values(), key=lambda t: (t.app_label, t.key))

    def for_app(self, app_label: str) -> list[TimeLimit]:
        return [t for t in self.all() if t.app_label == app_label]

    def __len__(self) -> int:
        return len(self._limits)

    def __iter__(self) -> Iterator[TimeLimit]:
        return iter(self.all())


#: The singleton every ``<app>/times.py`` registers into.
registry = TimeLimitRegistry()


# ---------------------------------------------------------------------------
# The façade — every function degrades to the free default, never raises
# ---------------------------------------------------------------------------

def free_seconds(key: str) -> int:
    """The default everyone gets. 0 for an unknown key (and a log line)."""
    decl = registry.get(key)
    if decl is None:
        logger.warning("times: unknown time-limit key %r", key)
        return 0
    return decl.free_seconds


def ceiling_seconds(key: str) -> int:
    decl = registry.get(key)
    return decl.ceiling_seconds if decl else 0


def _grant_seconds(key: str, *, user=None, scope_id=None):
    """The raw granted seconds, or None. Lazy and failure-tolerant."""
    from django.apps import apps

    # is_installed FIRST. A host can pin the economy wheel without installing
    # toto.tax — placidia does — and importing its models there raises
    # RuntimeError out of Django's model metaclass ("doesn't declare an
    # explicit app_label"), which `except ImportError` never catches.
    if not apps.is_installed("toto.tax"):
        return None
    try:
        from toto.tax.models import TimeGrant
    except ImportError:
        return None
    try:
        qs = TimeGrant.objects.filter(key=key)
        if scope_id is not None:
            qs = qs.filter(scope_id=scope_id)
        else:
            qs = qs.filter(scope_id__isnull=True)
            if user is None or not getattr(user, "pk", None):
                return None
            qs = qs.filter(user=user)
        row = qs.values_list("seconds", flat=True).first()
    except DatabaseError:
        # Mid-migrate, scratch shell, fresh deploy: the contract is a default
        # answer, not an exception out of a kernel reaper.
        return None
    return row


def effective_seconds(key: str, *, user=None, scope_id=None) -> int:
    """The limit that actually applies: max(free, min(grant, ceiling))."""
    decl = registry.get(key)
    if decl is None:
        logger.warning("times: unknown time-limit key %r", key)
        return 0
    granted = _grant_seconds(key, user=user, scope_id=scope_id)
    if granted is None:
        return decl.free_seconds
    return max(decl.free_seconds, min(int(granted), decl.ceiling_seconds))


def bulk_effective_seconds(key: str, *, scope_ids=None, user_ids=None) -> dict[int, int]:
    """Effective seconds keyed by scope_id (or user_id). Missing ids are simply
    absent — the caller falls back to the free default."""
    decl = registry.get(key)
    if decl is None:
        return {}
    from django.apps import apps

    if not apps.is_installed("toto.tax"):     # before the import — see above
        return {}
    try:
        from toto.tax.models import TimeGrant
    except ImportError:
        return {}
    try:
        if scope_ids is not None:
            rows = (TimeGrant.objects
                    .filter(key=key, scope_id__in=list(scope_ids))
                    .values_list("scope_id", "seconds"))
        else:
            rows = (TimeGrant.objects
                    .filter(key=key, scope_id__isnull=True,
                            user_id__in=list(user_ids or []))
                    .values_list("user_id", "seconds"))
        return {ident: max(decl.free_seconds, min(int(sec), decl.ceiling_seconds))
                for ident, sec in rows}
    except DatabaseError:
        return {}


def dial(key: str, *, user=None, scope_id=None) -> dict:
    """Everything a Time card needs, as plain data. ``set_url`` is "" on hosts
    with no economy, and a template hides the whole card on that."""
    decl = registry.get(key)
    if decl is None:
        return {}
    effective = effective_seconds(key, user=user, scope_id=scope_id)
    extension_seconds = max(0, effective - decl.free_seconds)
    return {
        "key": decl.key,
        "label": decl.label,
        "description": decl.description,
        "scope_id": scope_id,
        "free_seconds": decl.free_seconds,
        "ceiling_seconds": decl.ceiling_seconds,
        "effective_seconds": effective,
        "extension_hours": round(extension_seconds / 3600, 4),
        "display_unit": decl.display_unit,
        "set_url": _tax_url("tax:time_set"),
        # The roll-up of every dial is a section of the thing that bills held
        # time, so a card links there rather than at a page of its own.
        "manage_url": _quota_url("quota:metric_detail", "time.hold"),
        "priced": _hold_price() is not None,
        "daily_estimate": _daily_estimate(extension_seconds),
    }


def dials_for_user(user) -> dict:
    """Every dial this user holds above its free default, as plain data.

    The roll-up behind the ``time.hold`` section of the per-thing page: what am
    I holding across everything, and what does it cost me tonight. Lifted here
    from ``tax.timegrants.rows_for_user`` so the page that renders it needs no
    tax import — the same move ``rates.py`` made for prices.

    ``{"rows": [], "total_extension_hours": 0, "total_estimate": None}`` on a
    host with no economy, which renders as "no dials raised" rather than as an
    error. The per-app Time cards are unaffected: they call :func:`dial` for one
    key and POST to the same door, and this is only the summary of all of them.
    """
    empty = {"rows": [], "total_extension_hours": 0,
             "total_estimate": None}
    from django.apps import apps

    if not apps.is_installed("toto.tax"):     # before the import — see above
        return empty
    if user is None or not getattr(user, "is_authenticated", False):
        return empty
    try:
        from toto.tax import timegrants
    except ImportError:  # pragma: no cover
        return empty
    try:
        data = timegrants.rows_for_user(user)
    except DatabaseError:
        return empty

    rows = []
    for row in data.get("rows") or []:
        decl, grant = row["decl"], row["grant"]
        rows.append({
            # Flattened deliberately: a TimeGrant reaching a quota template is
            # what this façade exists to prevent.
            "key": grant.key,
            "scope_id": grant.scope_id,
            "seconds": grant.seconds,
            "label": decl.label,
            "scope_label": row["scope_label"],
            "free_seconds": decl.free_seconds,
            "ceiling_seconds": decl.ceiling_seconds,
            "extension_hours": row["extra_hours"],
            "estimate": row["estimate"],
        })
    return {
        "rows": rows,
        "total_extension_hours": data.get("total_extra_hours") or 0,
        "total_estimate": data.get("total_estimate"),
    }


def set_url() -> str:
    """The one POST door every dial writes through, "" where none exists."""
    return _tax_url("tax:time_set")


def clear_scope(scope_model: str, scope_id: int) -> int:
    """Drop every grant hanging off one scoped object (workspace teardown).
    Returns how many were removed; 0 when the economy is absent."""
    from django.apps import apps

    if not apps.is_installed("toto.tax"):     # before the import — see above
        return 0
    try:
        from toto.tax.models import TimeGrant
    except ImportError:
        return 0
    keys = [t.key for t in registry.all() if t.scope_model == scope_model]
    if not keys:
        return 0
    try:
        deleted, _detail = TimeGrant.objects.filter(
            key__in=keys, scope_id=scope_id).delete()
    except DatabaseError:
        return 0
    return deleted


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _tax_url(name: str) -> str:
    from django.apps import apps

    if not apps.is_installed("toto.tax"):
        return ""
    try:
        from django.urls import reverse

        return reverse(name)
    except Exception:  # noqa: BLE001 - unmounted namespace is a soft edge
        return ""


def _quota_url(name: str, *args) -> str:
    """A url in this app's own namespace, "" when it is not mounted.

    Guarded despite being local: a host can install toto.quota without giving
    it a URL prefix, and a Time card must degrade to no link rather than to a
    500 on somebody's workspace page.
    """
    try:
        from django.urls import reverse

        return reverse(name, args=args)
    except Exception:  # noqa: BLE001 - unmounted namespace is a soft edge
        return ""


def _hold_price() -> dict | None:
    from . import rates

    return rates.rate_card().get("time.hold")


def _daily_estimate(extension_seconds: int) -> dict | None:
    # The MARGINAL cost of this one dial's extension. Every raised second is
    # billable, so the roll-up on the metered thing is exactly the sum of the
    # rows — the two can no longer disagree.
    price = _hold_price()
    if price is None or extension_seconds <= 0:
        return None
    from decimal import Decimal

    hours = Decimal(extension_seconds) / Decimal(3600)
    unit_quantity = price["unit_quantity"] or Decimal("1")
    amount = hours * Decimal(price["price_display"]) / Decimal(unit_quantity)
    return {
        "amount": amount.quantize(Decimal(10) ** -price["asset_decimals"]),
        "asset": price["asset"],
    }
