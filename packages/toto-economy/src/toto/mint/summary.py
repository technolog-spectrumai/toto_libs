"""What the mint tab shows: per currency, and for the chain as a whole.

Kept out of ``views.py`` so it can be tested without a request, a template or
an authenticated user — the numbers here are the ones an operator decides to
mint on, and they deserve a test that does not go through HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .history import (chain_head, head_hash, maximum, supply, unminted,
                      verify_chain)


@dataclass
class CurrencyRow:
    """One engraved currency, with every number the tab needs about it."""

    asset: object
    supply_base_units: int
    max_supply_base_units: int
    unminted_base_units: int
    reserve_base_units: int
    events: int

    @property
    def supply(self) -> Decimal:
        from toto.assets.models import from_base_units

        return from_base_units(self.supply_base_units, self.asset.decimals)

    @property
    def max_supply(self) -> Decimal:
        from toto.assets.models import from_base_units

        return from_base_units(self.max_supply_base_units, self.asset.decimals)

    @property
    def unminted(self) -> Decimal:
        from toto.assets.models import from_base_units

        return from_base_units(self.unminted_base_units, self.asset.decimals)

    @property
    def reserve(self) -> Decimal:
        from toto.assets.models import from_base_units

        return from_base_units(self.reserve_base_units, self.asset.decimals)

    @property
    def fully_minted(self) -> bool:
        return self.unminted_base_units == 0

    @property
    def reserve_matches_supply(self) -> bool:
        """Is every unit that exists still sitting in the reserve?

        False is the ordinary case once anything has been distributed — this
        is a "where is it" indicator, not a health check. What supply must
        agree with is the chain, and it does by construction.
        """
        return self.reserve_base_units == self.supply_base_units


def currency_rows(assets=None) -> list:
    """Every locally-engraved currency, with its supply, ceiling and reserve.

    Mirrors are excluded: they are copies of somebody else's identity, and
    there is nothing here that could be minted or burned.
    """
    from toto.assets.models import Asset
    from toto.assets.queries import get_asset_balance

    from .models import CurrencyMintEvent

    if assets is None:
        assets = Asset.objects.filter(is_mirror=False).select_related(
            "reserve_account").order_by("unit_name")

    counts = {}
    for row in CurrencyMintEvent.objects.values("currency_hash"):
        counts[row["currency_hash"]] = counts.get(row["currency_hash"], 0) + 1

    rows = []
    for asset in assets:
        minted = supply(asset)
        rows.append(CurrencyRow(
            asset=asset,
            supply_base_units=minted,
            max_supply_base_units=maximum(asset),
            unminted_base_units=unminted(asset),
            reserve_base_units=(
                get_asset_balance(asset, asset.reserve_account)
                if asset.reserve_account_id else 0),
            events=counts.get(asset.currency_hash, 0),
        ))
    return rows


def chain_summary() -> dict:
    """The chain as a whole: its head, its length, and whether it holds."""
    from .models import CurrencyMintEvent

    head = chain_head()
    verdict = verify_chain()
    return {
        "head": head,
        "head_hash": head_hash(),
        "length": CurrencyMintEvent.objects.count(),
        "next_sequence": (head.sequence + 1) if head is not None else 0,
        "ok": verdict.ok,
        "findings": verdict.findings,
    }


def history(limit: int = 200):
    """The whole monetary history, newest first."""
    from .models import CurrencyMintEvent

    return (CurrencyMintEvent.objects.select_related("asset", "actor")
            .order_by("-sequence")[:limit])
