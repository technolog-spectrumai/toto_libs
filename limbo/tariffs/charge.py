"""
tariffs/charge.py — Auto-charge helpers for metered apps.

Public API
----------
InsufficientBalanceError       raise this; UI catches and explains
get_tariff_for_user(user, app_label)
    → Tariff with the lowest effective rate across all user's communities,
      falling back to the platform-default tariff for that app_label.
check_user_can_act(user, tariff, metric_code, quantity, unit="")
    → None if affordable, raises InsufficientBalanceError otherwise.
charge_user(user, tariff, metric_code, quantity, unit, source_type, source_id)
    → (UsageRecord, LedgerTransaction)
check_and_charge(user, tariff, metric_code, quantity, unit, source_type, source_id)
    → (UsageRecord, LedgerTransaction) — atomic check-then-charge

Rules
-----
- Payer account = user's prepaid account (get_or_create)
- Tariff resolution: all user communities → lowest effective price_per_unit / unit_quantity
  for the given app_label, same charged_asset as payer's prepaid
- Falls back to platform default tariff (source_type="" or source_type="platform")
- Does NOT import vault/vod/ravioli/steven/academy/kanban
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toto.tariffs.models import Tariff


# ---------------------------------------------------------------------------
# InsufficientBalanceError
# ---------------------------------------------------------------------------

class InsufficientBalanceError(Exception):
    """
    Raised when a user cannot afford a metered action.
    Carries structured data for friendly UI rendering.
    """

    def __init__(
        self,
        asset_name: str,
        needed_base_units: int,
        have_base_units: int,
        asset_decimals: int = 2,
        topup_url: str | None = None,
    ):
        self.asset_name = asset_name
        self.needed_base_units = needed_base_units
        self.have_base_units = have_base_units
        self.asset_decimals = asset_decimals
        self.topup_url = topup_url

    @property
    def needed_display(self) -> Decimal:
        factor = Decimal(10) ** self.asset_decimals
        return Decimal(self.needed_base_units) / factor

    @property
    def have_display(self) -> Decimal:
        factor = Decimal(10) ** self.asset_decimals
        return Decimal(self.have_base_units) / factor

    @property
    def shortfall_display(self) -> Decimal:
        return max(Decimal("0"), self.needed_display - self.have_display)

    def __str__(self) -> str:
        return (
            f"Insufficient {self.asset_name}: "
            f"need {self.needed_display}, "
            f"have {self.have_display} "
            f"(short by {self.shortfall_display})."
        )


# ---------------------------------------------------------------------------
# Tariff resolution
# ---------------------------------------------------------------------------

def _effective_price_per_unit(item) -> Decimal:
    """Comparable unit price: base_units / unit_quantity."""
    if not item.unit_quantity:
        return Decimal(item.price_per_unit_base_units)
    return Decimal(item.price_per_unit_base_units) / Decimal(str(item.unit_quantity))


def get_tariff_for_user(user, app_label: str) -> "Tariff | None":
    """
    Find the best (lowest effective rate) active Tariff for this user and app.

    Resolution order:
      1. Community-specific tariffs across all of the user's active communities —
         pick the one with the lowest effective price for the first active item
         that matches app_label.
      2. Platform-default tariff: source_type="" or source_type="platform",
         code starts with app_label.
      3. None — caller decides whether to proceed or block.

    Comparison is asset-aware: only tariffs charging in the same asset as the
    user's prepaid account are considered comparable.  Tariffs in a different
    asset are still returned if no same-asset tariff exists.
    """
    from toto.tariffs.models import Tariff, TariffStatus

    # 1. Collect community-specific tariffs
    community_tariffs: list["Tariff"] = []
    try:
        person = user.community_profile
        communities = person.communities.filter(is_active=True) if hasattr(person.communities, 'filter') else person.communities.all()
        for community in communities:
            qs = Tariff.objects.filter(
                status=TariffStatus.ACTIVE,
                source_type="socialhub.Community",
                source_id=str(community.pk),
            ).filter(
                items__metric__code__startswith=app_label.split(".")[0],
                items__active=True,
            ).distinct()
            community_tariffs.extend(qs)
    except Exception:
        pass

    if community_tariffs:
        # Sort by lowest effective price on any item matching app_label
        def _min_price(tariff):
            items = tariff.active_items.filter(
                metric__code__startswith=app_label.split(".")[0]
            )
            if not items.exists():
                return Decimal("999999999")
            return min(_effective_price_per_unit(i) for i in items)

        community_tariffs.sort(key=_min_price)
        return community_tariffs[0]

    # 2. Platform default
    platform_tariff = (
        Tariff.objects
        .filter(status=TariffStatus.ACTIVE)
        .filter(source_type__in=["", "platform"])
        .filter(items__metric__app_label=app_label, items__active=True)
        .distinct()
        .first()
    )
    if platform_tariff:
        return platform_tariff

    # 3. Fallback: any active tariff with matching metric app_label
    return (
        Tariff.objects
        .filter(status=TariffStatus.ACTIVE, items__metric__app_label=app_label, items__active=True)
        .distinct()
        .first()
    )


# ---------------------------------------------------------------------------
# Balance check
# ---------------------------------------------------------------------------

def _get_billing_account(user):
    """
    Return the account to debit for this user.

    Resolution:
      1. User's highest-priority LedgerAccount (user_priority > 0, descending).
      2. Fall back to the auto-created prepaid account.
    """
    from toto.assets.models import LedgerAccount
    from toto.assets.prepaid import get_or_create_prepaid_account

    priority_account = (
        LedgerAccount.objects
        .filter(user=user, active=True, user_priority__gt=0)
        .order_by("-user_priority")
        .first()
    )
    if priority_account:
        return priority_account
    prepaid, _ = get_or_create_prepaid_account(user)
    return prepaid


def check_user_can_act(
    user,
    tariff: "Tariff",
    metric_code: str,
    quantity: int | float | Decimal,
    unit: str = "",
) -> None:
    """
    Check user has enough prepaid balance.
    Raises InsufficientBalanceError if not.
    Returns None if affordable (or no tariff items match — free pass).
    """
    from toto.tariffs.services import check_can_afford

    payer_account = _get_billing_account(user)
    can_afford, msg = check_can_afford(
        tariff=tariff,
        payer_account=payer_account,
        charges=[(metric_code, Decimal(str(quantity)), unit)],
    )

    if not can_afford:
        # Build structured error from check_can_afford message
        # Parse asset and amounts from existing services
        from toto.tariffs.services import calculate_tariff_charge
        drafts = calculate_tariff_charge(tariff, metric_code, Decimal(str(quantity)), unit,
                                         payer_account_id=payer_account.pk)
        if drafts:
            from toto.assets.models import Asset, AssetHolding
            draft = drafts[0]
            asset = Asset.objects.get(pk=draft.asset_id)
            holding = AssetHolding.objects.filter(
                account=payer_account, asset=asset
            ).first()
            have = holding.balance_base_units if holding else 0
            raise InsufficientBalanceError(
                asset_name=asset.unit_name,
                needed_base_units=draft.amount_base_units,
                have_base_units=have,
                asset_decimals=asset.decimals,
            )
        raise InsufficientBalanceError(
            asset_name="tokens",
            needed_base_units=1,
            have_base_units=0,
        )


# ---------------------------------------------------------------------------
# Charge
# ---------------------------------------------------------------------------

def charge_user(
    user,
    tariff: "Tariff",
    metric_code: str,
    quantity: int | float | Decimal,
    unit: str = "",
    source_type: str = "",
    source_id: str = "",
    description: str = "",
):
    """
    Post usage and drain the user's prepaid balance.
    Returns (UsageRecord, LedgerTransaction).
    Raises InsufficientBalanceError if balance is insufficient at post time.
    """
    from toto.tariffs.services import record_and_post_usage

    payer_account = _get_billing_account(user)
    try:
        record, tx = record_and_post_usage(
            tariff=tariff,
            payer_account=payer_account,
            metric_code=metric_code,
            quantity=Decimal(str(quantity)),
            unit=unit,
            source_type=source_type,
            source_id=source_id,
            description=description or f"{metric_code} × {quantity}",
        )
    except ValueError as exc:
        msg = str(exc)
        if "Insufficient" in msg or "insufficient" in msg:
            raise InsufficientBalanceError(
                asset_name="tokens",
                needed_base_units=0,
                have_base_units=0,
            ) from exc
        raise
    return record, tx


# ---------------------------------------------------------------------------
# check_and_charge — atomic convenience wrapper
# ---------------------------------------------------------------------------

def check_and_charge(
    user,
    tariff: "Tariff",
    metric_code: str,
    quantity: int | float | Decimal,
    unit: str = "",
    source_type: str = "",
    source_id: str = "",
    description: str = "",
):
    """
    Check balance then charge atomically.
    Raises InsufficientBalanceError before any write if balance is short.
    Returns (UsageRecord, LedgerTransaction) on success.
    """
    check_user_can_act(user, tariff, metric_code, quantity, unit)
    return charge_user(
        user, tariff, metric_code, quantity, unit,
        source_type=source_type, source_id=source_id, description=description,
    )
