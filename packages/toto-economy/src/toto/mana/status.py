"""How a pool reads — band, trend, fill, and the encrypt pre-selection.

Pure functions over plain numbers, so the header chip, the JSON endpoint, the
pages and the tests share one rule and none of them needs a database to check
it.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, Decimal

#: At or under this share of the maximum a pool reads as low.
LOW_SHARE = Decimal("0.25")


def pct_of(amount, maximum) -> int:
    """0–100, for a bar's width. Never negative, never past full."""
    amount, maximum = Decimal(amount or 0), Decimal(maximum or 0)
    if maximum <= 0:
        return 0
    return max(0, min(100, int(amount * 100 / maximum)))


def band_of(amount, maximum) -> str:
    """``empty`` at zero, ``low`` at a quarter or less, otherwise ``ok``."""
    amount, maximum = Decimal(amount or 0), Decimal(maximum or 0)
    if amount <= 0:
        return "empty"
    if maximum > 0 and amount / maximum <= LOW_SHARE:
        return "low"
    return "ok"


def trend_of(amount, maximum, net_per_day) -> str:
    """``up``/``down``/``flat``. A pool pinned at an end has no trend: a bar
    at full with an up-arrow promises something that cannot happen."""
    amount, maximum, net = (Decimal(amount or 0), Decimal(maximum or 0),
                            Decimal(net_per_day or 0))
    if net < 0 and amount > 0:
        return "down"
    if net > 0 and amount < maximum:
        return "up"
    return "flat"


def eta_hours(amount, maximum, net_per_hour):
    """``(hours_to_full, hours_to_empty)`` at the current rate; either None."""
    amount, maximum, net = (Decimal(amount or 0), Decimal(maximum or 0),
                            Decimal(net_per_hour or 0))
    # An explicit ceiling: ``Decimal.__floordiv__`` truncates toward zero, so
    # the usual ``-(-a // b)`` rounds DOWN here and promises an hour too soon.
    if net > 0 and amount < maximum:
        return _ceil((maximum - amount) / net), None
    if net < 0 and amount > 0:
        return None, _ceil(amount / -net)
    return None, None


def _ceil(value: Decimal) -> int:
    return int(value.to_integral_value(rounding=ROUND_CEILING))


def preselect(files, regen_per_day) -> list:
    """Which plain files to tick so the pool stops draining — fewest first.

    Morion's prompt (`ManaPrompt.vue`): walk the files by drain, costliest
    first, ticking until what is left drains less than comes back. Always at
    least one, so the button is never offered with nothing chosen. ``files`` is
    ``[{"pk", "drain_per_day"}]``; returns the ticked pks.
    """
    ordered = sorted(files, key=lambda f: Decimal(f["drain_per_day"]), reverse=True)
    remaining = sum((Decimal(f["drain_per_day"]) for f in ordered), Decimal(0))
    regen = Decimal(regen_per_day or 0)
    picked = []
    for f in ordered:
        if picked and remaining < regen:
            break
        picked.append(f["pk"])
        remaining -= Decimal(f["drain_per_day"])
    return picked
