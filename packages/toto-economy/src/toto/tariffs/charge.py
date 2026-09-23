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

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from toto.tariffs.models import Tariff

logger = logging.getLogger("toto.tariffs")


# ---------------------------------------------------------------------------
# InsufficientBalanceError
# ---------------------------------------------------------------------------

class MonetaryAuthorityUnreachable(Exception):
    """The master could not be reached for something only the master can do.

    A SIBLING of InsufficientBalanceError, not a variant of "no price". That
    distinction is the whole point: toto.quota.charge documents that an
    unpriced metric and an unbilled host are the same thing to a caller and
    both mean FREE. Routing an outage through that sentinel would make a
    branch silently give everything away for the length of the outage, and no
    call site could tell.

    So this is an exception with its own status code. Every call site already
    reads ``exc.status_code``, so none of them need editing, and the state
    stays distinguishable in logs and in UI copy.

    Raised only by things that genuinely need the master — a top-up, a trade.
    NEVER by ordinary billing, which is local and works perfectly well while
    the master is down.
    """

    #: Service Unavailable. Distinct from 402 (you cannot pay) and 429 (you
    #: are over your cap): the platform cannot answer right now, and the
    #: honest thing is to say so rather than to improvise.
    status_code = 503

    def __init__(self, detail: str = ""):
        self.detail = detail or (
            "The monetary master cannot be reached, so this cannot be done "
            "here. Billing and spending are unaffected — they are local.")
        super().__init__(self.detail)

    def __str__(self):
        return self.detail


class InsufficientBalanceError(Exception):
    """
    Raised when a user cannot afford a metered action.
    Carries structured data for friendly UI rendering.
    """

    #: Payment Required — what an HTTP layer should answer with. Distinct from
    #: quota's 429: the caller is allowed to do this, they just cannot pay for
    #: it. toto.quota.charge's stub declares the same attribute, so a call site
    #: can read `exc.status_code` without knowing whether billing is installed.
    status_code = 402

    def __init__(
        self,
        asset_name: str,
        needed_base_units: int,
        have_base_units: int,
        asset_decimals: int = 2,
        topup_url: str | None = None,
        detail: str = "",
    ):
        self.asset_name = asset_name
        self.needed_base_units = needed_base_units
        self.have_base_units = have_base_units
        self.asset_decimals = asset_decimals
        self.topup_url = topup_url
        #: A sentence that replaces the generic one when set — how a mana pool
        #: explains itself ("Not enough security mana … refills 4 an hour").
        #: Every handler renders ``str(exc)``, so this is the one place the
        #: wording can change without touching twenty call sites.
        self.detail = detail

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
        if self.detail:
            return self.detail
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
    Find the Tariff that binds this user for this app.

    Resolution order:
      1. Platform-default tariff: source_type "" or "platform".
      2. Any active tariff with a matching item — the last-resort fallback.

    **There is no per-user or per-community branch, deliberately.** Prices do
    not vary by who is asking: one rate card, one treasury, and what varies is
    the QUANTITY a levy reports (a community's head weight) and the LIMIT a
    person may reach (an office's headroom). A price that changed per payer
    would put a second, thinner rating mechanism beside this one and make "what
    does this cost" a question with no single answer.

    History worth knowing: there WAS a community branch, and it was dead code
    from the day it was written. It filtered ``person.communities.filter(
    is_active=True)`` — a field ``Community`` has never had — so it raised
    FieldError on every call, swallowed by a bare ``except Exception``, and no
    community tariff ever applied to anybody on any host. It also matched items
    by metric-code PREFIX while every other branch matches
    ``metric__app_label``, so it could not have resolved for vault even if it
    ran. Nothing depended on it, because nothing could.
    """
    from toto.tariffs.models import Tariff, TariffStatus

    # 1. Platform default
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

    # 2. Fallback: any active tariff with matching metric app_label
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


def _mana_detail(user, asset, needed_base_units: int, have_base_units: int) -> str:
    """The mana wording for a refusal in a pool's asset, or "" — never raises.

    A refusal path must never become a 500 because the nicer sentence could
    not be built; the plain one is always there to fall back on.
    """
    from django.apps import apps

    if not apps.is_installed("toto.mana"):
        return ""
    try:
        from toto.mana.services import explain_shortfall

        return explain_shortfall(user, asset, needed_base_units, have_base_units) or ""
    except Exception:  # noqa: BLE001
        return ""


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
                detail=_mana_detail(user, asset, draft.amount_base_units, have),
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


# ---------------------------------------------------------------------------
# credit_user — the treasury pays the user (a negative-quantity subscription)
# ---------------------------------------------------------------------------

def credit_user(
    user,
    tariff: "Tariff",
    metric_code: str,
    quantity: int | float | Decimal,
    unit: str = "",
    source_type: str = "",
    source_id: str = "",
    description: str = "",
    reference: str = "",
):
    """Pay the user from the treasury. Returns the LedgerTransaction, or None.

    The other direction from :func:`charge_user`, for a subscription whose
    quantity is negative — a stipend. ``quantity`` is the magnitude, always
    positive: the caller read the sign and chose this function because of it.

    **This deliberately does not go through the usage pipeline.**
    ``record_and_post_usage`` is built around "the payer's holding is debited,
    the item's ``receiving_account`` is credited", and inverting it would mean
    inverting rating, the balance lock and the insufficient-funds path — three
    places where money would be wrong if the inversion were subtly off. Instead
    this prices the work with the same pure calculator the charge path uses
    (:func:`calculate_tariff_charge`) and then moves the money the plain way:
    one ``transfer_asset`` out of the revenue account. The retired treasury
    payroll moved a stipend exactly like this, and ``assets.services.faucets``
    moves an hourly payout the same way now.

    ``reference`` is the idempotency key and callers should pass a stable one —
    ``transfer_asset`` refuses a duplicate, which is what makes paying the same
    period twice impossible rather than merely unlikely.
    """
    from django.core.exceptions import ValidationError

    from toto.assets.models import from_base_units
    from toto.assets.services.assets import transfer_asset
    from toto.tariffs.rate_card import revenue_account
    from toto.tariffs.services import calculate_tariff_charge

    drafts = calculate_tariff_charge(tariff, metric_code, Decimal(str(quantity)), unit)
    if not drafts:
        # Unpriced metric — free, exactly as the charge path reads it. Nothing
        # to pay, and that is not a failure.
        return None

    total = sum(d.amount_base_units for d in drafts)
    if total <= 0:
        return None

    asset = drafts[0].tariff_item.charged_asset
    payer = revenue_account()
    payee = _get_billing_account(user)

    try:
        return transfer_asset(
            asset=asset,
            sender_account=payer,
            receiver_account=payee,
            amount=from_base_units(total, asset.decimals),
            reference=reference or f"credit-{metric_code}-{source_type}-{source_id}",
            description=description or f"{metric_code} × {quantity} (credit)",
            metadata={"kind": "credit", "metric_code": metric_code,
                      "source_type": source_type, "source_id": str(source_id)},
        )
    except ValidationError:
        # Almost always an empty treasury, which is the same condition payroll
        # meets and treats as "still owed". Let the caller decide: it holds the
        # row that records the debt.
        raise


# ---------------------------------------------------------------------------
# refund_usage_record — undo a charge for work that failed
# ---------------------------------------------------------------------------

def refund_usage_record(usage_record, *, reference: str = "", description: str = ""):
    """Reverse a posted charge and mark the record reversed.

    For the async case: the user paid on submit, then the job failed, so the
    resource they bought was never delivered.

    Guarded, because ``reverse_transaction`` does no balance check of its own —
    it would happily drive the receiving account negative if the platform had
    already spent what it collected. Returns the reversing transaction, or None
    when there is nothing (or nothing safe) to undo.
    """
    from django.core.exceptions import ValidationError
    from django.db import transaction as db_transaction

    from toto.assets.models import AssetHolding
    from toto.assets.services.assets import reverse_transaction

    from .models import UsageStatus

    if usage_record is None or usage_record.status != UsageStatus.POSTED:
        return None
    tx = usage_record.ledger_transaction
    if tx is None or getattr(tx, "reversal_set", None) and tx.reversal_set.exists():
        return None

    with db_transaction.atomic():
        # Every account that was credited must still hold what it received.
        for entry in tx.entries.select_related("account", "asset").all():
            if entry.amount_base_units <= 0:
                continue
            holding = AssetHolding.objects.filter(
                asset=entry.asset, account=entry.account
            ).first()
            if holding is None or holding.balance_base_units < entry.amount_base_units:
                logger.warning(
                    "tariffs: refusing to refund %s — account %s has spent the credit",
                    usage_record.uuid, entry.account.code,
                )
                return None

        try:
            reversal = reverse_transaction(
                transaction=tx,
                reference=reference or f"refund-{usage_record.uuid}",
                description=description or f"Refund for failed {usage_record.metric_code}",
            )
        except ValidationError as exc:
            logger.warning("tariffs: could not refund %s: %s", usage_record.uuid, exc)
            return None

        usage_record.status = UsageStatus.REVERSED
        usage_record.save(update_fields=["status"])
        return reversal
