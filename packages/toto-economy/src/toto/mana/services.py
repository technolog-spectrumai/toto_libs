"""The mana app's read and write surface. Every model import is lazy.

Everything a member sees is read from the ordinary ledger: a pool's balance is
the member's ``AssetHolding`` in that pool's asset, and a pool's history is the
``LedgerEntry`` rows that moved it. Nothing here stores a second number that
could disagree with the ledger.
"""

from __future__ import annotations

from . import colours


# ---------------------------------------------------------------------------
# The bindings
# ---------------------------------------------------------------------------

def pools() -> dict:
    """``{role: ManaPool}`` — empty before the first ingress, or on a branch."""
    from .models import ManaPool

    return {p.role: p for p in ManaPool.objects.select_related("asset")}


def roles() -> dict:
    """``{role: Asset}`` — the functional bindings."""
    return {role: pool.asset for role, pool in pools().items()}


def colour_of(metric_code: str) -> str | None:
    return colours.COLOUR_OF.get(metric_code)


def asset_for(metric_code: str):
    """The asset a metric is priced in, or None when it is not mana."""
    role = colour_of(metric_code)
    if role is None:
        return None
    pool = pools().get(role)
    return pool.asset if pool is not None else None


def pool_asset_ids() -> set:
    """Pks of the assets pools are bound to — empty where no pool exists."""
    return {pool.asset_id for pool in pools().values()}


def pooled_codes() -> set:
    """Metric codes that a pool on THIS host can price.

    Not simply ``COLOUR_OF``: on a host with the app but no pools yet (a branch,
    or before the first issue) a mapped metric has no pool to be priced in, and
    the gas rate card must still be free to price it.
    """
    present = set(pools())
    return {code for code, role in colours.COLOUR_OF.items() if role in present}


def is_mana_asset(asset) -> str | None:
    """The role an asset is bound to, or None."""
    if asset is None:
        return None
    for role, pool in pools().items():
        if pool.asset_id == asset.pk:
            return role
    return None


# ---------------------------------------------------------------------------
# Prices
# ---------------------------------------------------------------------------

def seed_prices() -> dict:
    """``{code: Decimal}`` to seed: ``colours.PRICES`` with the host's
    ``MANA_PRICES`` over it (``None`` removes one). ``MANA_SEED_PRICES = False``
    seeds none — the same two dials ``ingress_tariffs`` offers for gas."""
    from decimal import Decimal

    from django.conf import settings

    if not getattr(settings, "MANA_SEED_PRICES", True):
        return {}
    prices = dict(colours.PRICES)
    for code, value in (getattr(settings, "MANA_PRICES", None) or {}).items():
        if value is None:
            prices.pop(code, None)
        else:
            prices[code] = Decimal(str(value))
    return prices


def audit() -> tuple[list, list]:
    """``(undecided, mapped_but_not_metered_here)``.

    Undecided — registered here, neither coloured nor exempted — is the one that
    matters: such a metric runs free, silently. The second list is information:
    a host that does not install mail has nothing to price for it.
    """
    from toto.quota.metrics import registry

    registered = set(registry.codes())
    undecided = sorted(registered - set(colours.COLOUR_OF) - colours.NOT_MANA)
    absent = sorted(set(colours.COLOUR_OF) - registered)
    return undecided, absent
