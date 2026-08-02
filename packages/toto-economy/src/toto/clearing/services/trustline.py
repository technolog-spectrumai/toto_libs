"""Trustlines: which assets cross the wire, and the accounts that carry them.

Home side (``issued_here=True``): the peer's aggregate position lives in a
``vostro`` external account — value sent to the peer moves INTO it, value the
peer sends back moves OUT of it toward the receiving user. Its balance IS the
bilateral position for that asset, which is what the credit limit bounds.

Mirror side: the asset row is created here with the same unit_name and a
reserve big enough to issue mirrored circulation from; nothing but clearing
ever mints from it, so mirror supply always equals the vostro position on the
home side — the invariant the nightly checkpoint verifies.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from toto.assets.models import Asset, AccountType, AssetHolding, LedgerAccount
from toto.assets.queries import get_asset_balance

from ..models import ClearingHold, LedgerPeer, SharedAsset

# The mirror-side issuance pool. Big on purpose: minting a mirror unit is
# always backed one-for-one by the home vostro, so the cap never binds — it
# exists because Asset requires a finite supply.
MIRROR_SUPPLY = Decimal("1000000000")


def _account(code: str, name: str, account_type: str) -> LedgerAccount:
    account, _ = LedgerAccount.objects.get_or_create(
        code=code, defaults={"name": name, "account_type": account_type})
    return account


def open_trustline(*, asset: Asset, peer: LedgerPeer, issued_here: bool,
                   max_owed: Decimal, settlement_threshold: Decimal | None = None,
                   ) -> SharedAsset:
    """Create (or fetch) the trustline and its system accounts."""
    from toto.assets.models import to_base_units

    with transaction.atomic():
        shared, created = SharedAsset.objects.select_for_update().get_or_create(
            asset=asset, peer=peer,
            defaults={
                "issued_here": issued_here,
                "max_owed_base_units": to_base_units(max_owed, asset.decimals),
                "settlement_threshold_base_units": to_base_units(
                    settlement_threshold if settlement_threshold is not None
                    else max_owed / 2, asset.decimals),
            },
        )
        if not created:
            return shared

        unit = asset.unit_name
        shared.escrow_account = _account(
            f"clearing:escrow:{unit}", f"Clearing escrow ({unit})",
            AccountType.SYSTEM)
        if issued_here:
            shared.vostro_account = _account(
                f"clearing:vostro:{peer.platform_id}:{unit}",
                f"{peer.platform_id} position ({unit})", AccountType.EXTERNAL)
        shared.save(update_fields=["escrow_account", "vostro_account",
                                   "updated_at"])
        return shared


def ensure_mirror_asset(*, unit_name: str, decimals: int, peer: LedgerPeer,
                        max_owed: Decimal) -> SharedAsset:
    """Mirror side: create the same-ticker asset (if absent) and its trustline.

    The reserve is the issuance pool clearing mints mirrored circulation from;
    a pre-existing asset with this ticker that is NOT clearing-managed is
    refused — that ticker belongs to a private local asset and sharing it would
    make one name mean two things (the exact bug the old doctrine prevented).
    """
    from toto.assets.services.assets import create_asset

    existing = Asset.objects.filter(unit_name=unit_name).first()
    if existing is not None:
        shared = SharedAsset.objects.filter(asset=existing, peer=peer).first()
        if shared is None:
            raise ValidationError(
                f"Asset {unit_name} already exists locally and is not shared "
                "with this peer — refusing to overload the ticker.")
        return shared

    reserve = _account(f"clearing:reserve:{unit_name}",
                       f"Clearing mirror reserve ({unit_name})",
                       AccountType.RESERVE)
    asset = create_asset(
        name=f"{unit_name} (mirror)", unit_name=unit_name,
        total_supply=MIRROR_SUPPLY, decimals=decimals,
        reserve_account=reserve, reference=f"clearing-mirror-{unit_name}",
    )
    # create_asset credits the reserve holding but leaves the FK unset; the
    # mirror issuance path (distribute_asset) requires it.
    asset.reserve_account = reserve
    asset.save(update_fields=["reserve_account"])
    return open_trustline(asset=asset, peer=peer, issued_here=False,
                          max_owed=max_owed)


def net_position_base(shared: SharedAsset) -> int:
    """The bilateral position for this asset, in base units.

    Home side: the vostro balance (what the peer's platform holds of ours).
    Mirror side: the circulating mirrored supply (issued minus returned).
    """
    if shared.issued_here:
        return get_asset_balance(shared.asset, shared.vostro_account)
    reserve_balance = get_asset_balance(shared.asset,
                                        shared.asset.reserve_account)
    from toto.assets.models import to_base_units
    total = to_base_units(MIRROR_SUPPLY, shared.asset.decimals)
    return total - reserve_balance


def check_credit(shared: SharedAsset, amount_base: int) -> None:
    """Refuse a movement that would push the position past max_owed."""
    if not shared.enabled:
        raise ValidationError(f"Trustline for {shared.asset.unit_name} is disabled.")
    if shared.peer.status != LedgerPeer.STATUS_ACTIVE:
        raise ValidationError("Peer is not active.")
    position = net_position_base(shared)
    if position + amount_base > shared.max_owed_base_units:
        raise ValidationError(
            f"Credit limit: position {position} + {amount_base} would exceed "
            f"max_owed {shared.max_owed_base_units} for {shared.asset.unit_name}.")


def pending_escrow_base(shared: SharedAsset) -> int:
    """Sum of value parked in escrow for still-pending holds — reconciliation
    helper: must always equal the escrow account balance."""
    from django.db.models import Sum

    return (ClearingHold.objects.filter(
        shared_asset=shared, state=ClearingHold.PENDING)
        .aggregate(s=Sum("amount_base_units"))["s"] or 0)
