"""The community fee: a surplus tax on asset holdings — demurrage on money.

It prevents idle accounts. Currency × time is taxed: whatever a user holds
above the per-asset threshold is charged a staff-set percentage each
calendar-aligned period, automatically, IN THE SAME ASSET, taken from their
largest holding first and collected into the ``platform-community-fees``
system account. Money should move.

Unlike the other levies this one can never fail to collect — the surplus it
taxes is what pays it — so there is no arrears, no warning, no enforcement.
The machinery is a materialize-then-pay sweep (the portfolio tribute shape):
a :class:`~toto.tax.models.SurplusCharge` row is created once per
(policy, user, period) with a deterministic largest-first allocation, and the
transfers ride ``transfer_asset``'s idempotent references, so any crash or a
double-fired beat replays to the identical outcome. Every compromise in the
failure paths UNDER-collects, never over-.

Who is taxed: USER-type, user-owned, active ledger accounts only.
SYSTEM/RESERVE/EXTERNAL and userless accounts are exempt by construction —
portfolio company wallets (EXTERNAL, user=None) stay untaxed. Note that
``user_priority`` deliberately plays no role here: billing picks one account
by priority, but the fee sums ALL of a user's accounts and collects
largest-first, which is what makes account-splitting pointless.
"""

from __future__ import annotations

import logging
from decimal import ROUND_DOWN, Decimal

from django.utils import timezone

logger = logging.getLogger("toto.tax")

COMMUNITY_FEE_ACCOUNT_CODE = "platform-community-fees"


def get_or_create_community_fee_account():
    from toto.assets.models import AccountType, LedgerAccount

    account, _created = LedgerAccount.objects.get_or_create(
        code=COMMUNITY_FEE_ACCOUNT_CODE,
        defaults={"name": "Platform Community Fees",
                  "account_type": AccountType.SYSTEM, "active": True,
                  "metadata": {"purpose": "community-fee"}},
    )
    return account


def period_label_for(policy, now=None) -> str:
    """``monthly:2026-08-01`` — the period kind is embedded so a staff period
    change can never collide labels (one overlapping window may double-charge
    once; the desk says so)."""
    from toto.quota.api import period_start

    start = period_start(policy.period, now or timezone.now())
    return f"{policy.period}:{start.date().isoformat()}"


def charge_reference(policy_pk: int, user_pk: int, period_label: str,
                     account_pk: int) -> str:
    return f"tax-surplus-{policy_pk}-{user_pk}-{period_label}-{account_pk}"


def fee_for(surplus_base: int, rate: Decimal) -> int:
    """floor(surplus × rate) — the dust stays with the user."""
    return int((Decimal(surplus_base) * rate).to_integral_value(rounding=ROUND_DOWN))


def decimal_text(value: Decimal) -> str:
    """A Decimal without trailing zeros and never scientific notation —
    normalize() would render 100 as 1E+2 in a sentence for a person."""
    text = str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def rate_pct_text(rate: Decimal) -> str:
    """'2', '0.5', '10' — the stored fraction as a percent, for humans."""
    return decimal_text(rate * 100)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------

def run_surplus_sweep(now=None) -> dict:
    """Collect the community fee for every active policy. Idempotent."""
    from .models import SurplusCharge, SurplusChargeStatus, SurplusPolicy

    now = now or timezone.now()
    summary: dict[str, dict] = {}

    for policy in SurplusPolicy.objects.filter(active=True).select_related("asset"):
        counts = {"charged": 0, "replayed": 0, "skipped_zero": 0, "pending": 0}
        summary[policy.asset.unit_name] = counts

        # Replay first: charges left PENDING by a freeze or a crash — any
        # period label — retry on every run until they land.
        for charge in SurplusCharge.objects.filter(
                policy=policy, status=SurplusChargeStatus.PENDING):
            if _execute_charge(policy, charge):
                counts["replayed"] += 1
            else:
                counts["pending"] += 1

        label = period_label_for(policy, now)
        for user_id, total in _surplus_totals(policy):
            if SurplusCharge.objects.filter(policy=policy, user_id=user_id,
                                            period_label=label).exists():
                continue
            surplus_base = total - policy.threshold_base_units
            fee_base = fee_for(surplus_base, policy.rate)
            if fee_base <= 0:
                counts["skipped_zero"] += 1
                continue
            charge = _materialize(policy, user_id, label, total, fee_base)
            if charge is None:  # a concurrent beat won the unique-constraint race
                continue
            if _execute_charge(policy, charge):
                counts["charged"] += 1
            else:
                counts["pending"] += 1
    return summary


def _surplus_totals(policy):
    """(user_id, total_base) for users over the threshold. One query."""
    from django.db.models import Sum

    from toto.assets.models import AccountType, AssetHolding

    return (AssetHolding.objects
            .filter(asset=policy.asset,
                    account__account_type=AccountType.USER,
                    account__user__isnull=False,
                    account__active=True,
                    balance_base_units__gt=0)
            .values_list("account__user")
            .annotate(total=Sum("balance_base_units"))
            .filter(total__gt=policy.threshold_base_units))


def _user_holdings(policy, user_id):
    """[(account_id, balance_base)] largest-first, pk tiebreak — deterministic."""
    from toto.assets.models import AccountType, AssetHolding

    return list(AssetHolding.objects
                .filter(asset=policy.asset,
                        account__account_type=AccountType.USER,
                        account__user_id=user_id,
                        account__active=True,
                        balance_base_units__gt=0)
                .order_by("-balance_base_units", "account_id")
                .values_list("account_id", "balance_base_units"))


def _allocate(holdings, fee_base: int) -> list[list[int]]:
    allocation = []
    remaining = fee_base
    for account_id, balance in holdings:
        if remaining <= 0:
            break
        take = min(remaining, balance)
        allocation.append([account_id, take])
        remaining -= take
    return allocation


def _materialize(policy, user_id, label, total_base, fee_base):
    from django.db import IntegrityError

    from .models import SurplusCharge

    allocation = _allocate(_user_holdings(policy, user_id), fee_base)
    try:
        return SurplusCharge.objects.create(
            policy=policy, user_id=user_id, period_label=label,
            total_base=total_base,
            threshold_base=policy.threshold_base_units,
            fee_base=fee_base, rate=policy.rate,
            allocation=allocation,
        )
    except IntegrityError:
        return None


def _execute_charge(policy, charge) -> bool:
    """Run (or re-run) a charge's allocation. True when it reached a terminal
    state. Rules: with NO leg posted yet, the allocation may be recomputed
    from current balances (no reference is burned, so no fingerprint can
    conflict); once ANY leg posted, the stored allocation replays verbatim
    and a leg that can no longer pay is written off in ``note`` — the fee
    never chases money that moved."""
    from django.core.exceptions import ValidationError

    from toto.assets.models import LedgerTransaction, from_base_units
    from toto.assets.services.assets import transfer_asset

    from .models import SurplusChargeStatus

    asset = policy.asset
    fee_account = get_or_create_community_fee_account()
    refs = [charge_reference(policy.pk, charge.user_id, charge.period_label, acc)
            for acc, _amt in charge.allocation]
    any_posted = LedgerTransaction.objects.filter(reference__in=refs).exists()

    if not any_posted:
        # Balances may have moved since materialization; shrink to what the
        # surplus still supports. Cap ≤ 0 → the surplus is gone: skip.
        holdings = _user_holdings(policy, charge.user_id)
        current_total = sum(balance for _acc, balance in holdings)
        cap = current_total - charge.threshold_base
        if cap <= 0:
            charge.status = SurplusChargeStatus.SKIPPED
            charge.note = {"reason": "surplus gone before collection"}
            charge.save(update_fields=["status", "note"])
            return True
        fee = min(charge.fee_base, cap)
        if fee != charge.fee_base or _allocate(holdings, fee) != charge.allocation:
            charge.fee_base = fee
            charge.allocation = _allocate(holdings, fee)
            charge.save(update_fields=["fee_base", "allocation"])

    shortfalls = []
    description = (f"Community fee: {rate_pct_text(charge.rate)}% per "
                   f"{policy.period} on {asset.unit_name} above "
                   f"{decimal_text(policy.threshold_display)}")
    from django.apps import apps as django_apps

    account_model = django_apps.get_model("assets", "LedgerAccount")
    for account_id, amount_base in charge.allocation:
        if amount_base <= 0:
            continue
        try:
            transfer_asset(
                asset=asset,
                sender_account=account_model.objects.get(pk=account_id),
                receiver_account=fee_account,
                amount=from_base_units(amount_base, asset.decimals),
                reference=charge_reference(policy.pk, charge.user_id,
                                           charge.period_label, account_id),
                description=description,
                metadata={"charge_pk": charge.pk},
            )
        except ValidationError as exc:
            if not any_posted and not shortfalls:
                # Nothing collected at all (freeze, inactive account): stay
                # PENDING and let tomorrow's replay try again.
                logger.warning(
                    "tax: community fee for user %s on %s stays pending: %s",
                    charge.user_id, asset.unit_name, exc)
                return False
            # Partially collected: write the leg off; never chase it.
            shortfalls.append({"account_id": account_id,
                               "amount_base": amount_base,
                               "error": str(exc)})
        else:
            any_posted = True
        # SoftTimeLimitExceeded is not a ValidationError — it propagates.

    charge.status = SurplusChargeStatus.COLLECTED
    charge.collected_at = timezone.now()
    if shortfalls:
        charge.note = {"shortfalls": shortfalls}
    charge.save(update_fields=["status", "collected_at", "note"])
    return True


# ---------------------------------------------------------------------------
# UI feeds — plain data only
# ---------------------------------------------------------------------------

def estimate_for_user(user) -> list[dict]:
    """One row per active policy: the dial, and the viewer's numbers."""
    from toto.assets.models import from_base_units

    from .models import SurplusPolicy

    rows = []
    for policy in SurplusPolicy.objects.filter(active=True).select_related("asset"):
        total = _user_total(policy, user)
        surplus = max(0, total - policy.threshold_base_units)
        fee = fee_for(surplus, policy.rate)
        rows.append({
            "asset_name": policy.asset.name,
            "unit": policy.asset.unit_name,
            "threshold": policy.threshold_display,
            "rate_pct": rate_pct_text(policy.rate),
            "period": policy.period,
            "total": from_base_units(total, policy.asset.decimals),
            "surplus": from_base_units(surplus, policy.asset.decimals),
            "estimate": from_base_units(fee, policy.asset.decimals),
        })
    return rows


def wallet_fee_map(user) -> dict:
    """{asset_id: plain dict} for the wallet's per-holding fee line."""
    from .models import SurplusPolicy

    by_unit = {row["unit"]: row for row in estimate_for_user(user)}
    return {policy.asset_id: by_unit[policy.asset.unit_name]
            for policy in SurplusPolicy.objects.filter(active=True).select_related("asset")
            if policy.asset.unit_name in by_unit}


def _user_total(policy, user) -> int:
    from django.db.models import Sum

    from toto.assets.models import AccountType, AssetHolding

    if user is None or not getattr(user, "pk", None):
        return 0
    agg = (AssetHolding.objects
           .filter(asset=policy.asset,
                   account__account_type=AccountType.USER,
                   account__user=user,
                   account__active=True)
           .aggregate(total=Sum("balance_base_units")))
    return int(agg["total"] or 0)
