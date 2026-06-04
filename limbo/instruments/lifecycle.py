"""
Idempotent lifecycle sync for financial instruments.

Creates/updates Schedule, Condition, Allocation, Entitlement, and ContractEvent
records keyed by source_type + source_id so repeated calls are safe.

No ledger balances are mutated here.
"""
from __future__ import annotations

from toto.claims.models import (
    Allocation,
    AllocationKind,
    AllocationStatus,
    Condition,
    ConditionKind,
    ConditionStatus,
    ContractEventKind,
    Entitlement,
    EntitlementKind,
    EntitlementStatus,
    Schedule,
    ScheduleKind,
    ScheduleStatus,
    ContractEvent,
)
from toto.claims.services.lifecycle import create_event

_INSTR_SRC = "instruments.FinancialInstrument"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _src(instrument):
    return _INSTR_SRC, str(instrument.pk)


def _get_or_create_entitlement(*, holder_account, kind, resource_label, source_type, source_id, **defaults):
    obj, created = Entitlement.objects.get_or_create(
        source_type=source_type,
        source_id=source_id,
        kind=kind,
        defaults=dict(
            holder_account=holder_account,
            resource_label=resource_label,
            status=EntitlementStatus.ACTIVE,
            **defaults,
        ),
    )
    return obj, created


def _get_or_create_schedule(*, name, kind, source_type, source_id, starts_at, **defaults):
    obj, created = Schedule.objects.get_or_create(
        source_type=source_type,
        source_id=source_id,
        kind=kind,
        name=name,
        defaults=dict(
            status=ScheduleStatus.ACTIVE,
            starts_at=starts_at,
            **defaults,
        ),
    )
    return obj, created


def _get_or_create_condition(*, name, kind, source_type, source_id, **defaults):
    obj, created = Condition.objects.get_or_create(
        source_type=source_type,
        source_id=source_id,
        kind=kind,
        name=name,
        defaults=dict(
            status=ConditionStatus.PENDING,
            **defaults,
        ),
    )
    return obj, created


def _get_or_create_allocation(*, kind, source_type, source_id, asset, amount_base_units, **defaults):
    obj, created = Allocation.objects.get_or_create(
        source_type=source_type,
        source_id=source_id,
        kind=kind,
        defaults=dict(
            asset=asset,
            amount_base_units=amount_base_units,
            status=AllocationStatus.ACTIVE,
            allocated_amount_base_units=amount_base_units,
            **defaults,
        ),
    )
    return obj, created


def _ensure_created_event(source_type, source_id, title, **kwargs):
    """Create a CREATED ContractEvent only once per source."""
    exists = ContractEvent.objects.filter(
        source_type=source_type,
        source_id=source_id,
        kind=ContractEventKind.CREATED,
    ).exists()
    if not exists:
        return create_event(
            kind=ContractEventKind.CREATED,
            title=title,
            source_type=source_type,
            source_id=source_id,
            **kwargs,
        )
    return None


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def sync_lifecycle_for_instrument(instrument) -> dict:
    """Dispatch to the per-type sync function.  Returns a summary dict."""
    from toto.instruments import models as _m

    itype = instrument.instrument_type
    try:
        if itype == "amortization":
            amort = _m.AmortizationContract.objects.select_related(
                "source_account", "destination_account", "asset"
            ).get(instrument=instrument)
            return sync_amortization_lifecycle(amort)

        if itype == "vesting":
            vest = _m.VestingContract.objects.select_related(
                "grantor_account", "beneficiary_account", "asset"
            ).get(instrument=instrument)
            return sync_vesting_lifecycle(vest)

        if itype == "escrow":
            escrow = _m.EscrowContract.objects.select_related(
                "buyer_account", "seller_account", "escrow_account", "asset"
            ).get(instrument=instrument)
            return sync_escrow_lifecycle(escrow)

        if itype == "forward":
            fwd = _m.ForwardContract.objects.select_related(
                "buyer_account", "seller_account", "underlying_asset", "payment_asset"
            ).get(instrument=instrument)
            return sync_forward_lifecycle(fwd)

        if itype == "option":
            opt = _m.OptionContract.objects.select_related(
                "buyer_account", "writer_account", "underlying_asset", "payment_asset"
            ).get(instrument=instrument)
            return sync_option_lifecycle(opt)

        if itype == "staking":
            stk = _m.StakingPosition.objects.select_related(
                "staker_account", "staking_account", "staked_asset", "reward_asset"
            ).get(instrument=instrument)
            return sync_staking_lifecycle(stk)

        if itype == "revenue_share":
            rs = _m.RevenueShareContract.objects.select_related(
                "revenue_asset", "revenue_account"
            ).prefetch_related("recipients__account").get(instrument=instrument)
            return sync_revenue_share_lifecycle(rs)

    except Exception:
        raise

    return {}


# ---------------------------------------------------------------------------
# Per-type sync functions
# ---------------------------------------------------------------------------

def sync_amortization_lifecycle(amort) -> dict:
    instr = amort.instrument
    src_type, src_id = _src(instr)

    ent, _ = _get_or_create_entitlement(
        holder_account=amort.destination_account,
        kind=EntitlementKind.USAGE_RIGHT,
        resource_label=f"Amortization right: {instr.reference}",
        source_type=src_type,
        source_id=src_id,
        starts_at=amort.starts_at,
        ends_at=amort.ends_at,
    )
    alloc, _ = _get_or_create_allocation(
        kind=AllocationKind.PREPAID_BALANCE,
        source_type=src_type,
        source_id=src_id,
        asset=amort.asset,
        amount_base_units=amort.original_amount_base_units,
        holder_account=amort.source_account,
        beneficiary_account=amort.destination_account,
    )
    _ensure_created_event(
        src_type, src_id,
        f"Amortization created: {instr.reference}",
        allocation=alloc,
        entitlement=ent,
    )
    return {"entitlements": 1, "allocations": 1}


def sync_vesting_lifecycle(vesting) -> dict:
    instr = vesting.instrument
    src_type, src_id = _src(instr)

    ent, _ = _get_or_create_entitlement(
        holder_account=vesting.beneficiary_account,
        kind=EntitlementKind.VESTING_RIGHT,
        resource_label=f"Vesting right: {instr.reference}",
        source_type=src_type,
        source_id=src_id,
        starts_at=vesting.start_at,
        ends_at=vesting.end_at,
    )
    alloc, _ = _get_or_create_allocation(
        kind=AllocationKind.VESTING_POOL,
        source_type=src_type,
        source_id=src_id,
        asset=vesting.asset,
        amount_base_units=vesting.total_amount_base_units,
        holder_account=vesting.grantor_account,
        beneficiary_account=vesting.beneficiary_account,
    )
    cliff = vesting.cliff_at or vesting.start_at
    sched, _ = _get_or_create_schedule(
        name=f"Release schedule: {instr.reference}",
        kind=ScheduleKind.VESTING,
        source_type=src_type,
        source_id=src_id,
        starts_at=cliff,
        ends_at=vesting.end_at,
        frequency=vesting.release_frequency,
        next_run_at=cliff,
    )
    cond_expr = (
        {"type": "now_after", "datetime": vesting.cliff_at.isoformat()}
        if vesting.cliff_at
        else {}
    )
    cond, _ = _get_or_create_condition(
        name=f"Cliff reached: {instr.reference}",
        kind=ConditionKind.TIME,
        source_type=src_type,
        source_id=src_id,
        description="Vesting cliff has been reached.",
        expression=cond_expr,
    )
    _ensure_created_event(
        src_type, src_id,
        f"Vesting created: {instr.reference}",
        schedule=sched,
        allocation=alloc,
        entitlement=ent,
    )
    return {"entitlements": 1, "allocations": 1, "schedules": 1, "conditions": 1}


def sync_escrow_lifecycle(escrow) -> dict:
    instr = escrow.instrument
    src_type, src_id = _src(instr)

    alloc, _ = _get_or_create_allocation(
        kind=AllocationKind.ESCROW_HOLD,
        source_type=src_type,
        source_id=src_id,
        asset=escrow.asset,
        amount_base_units=escrow.amount_base_units,
        holder_account=escrow.escrow_account,
        beneficiary_account=escrow.seller_account,
    )
    cond, _ = _get_or_create_condition(
        name=f"Release condition: {instr.reference}",
        kind=ConditionKind.MANUAL,
        source_type=src_type,
        source_id=src_id,
        description=escrow.release_condition or "Escrow release/refund condition.",
    )
    _ensure_created_event(
        src_type, src_id,
        f"Escrow created: {instr.reference}",
        allocation=alloc,
        condition=cond,
    )
    return {"allocations": 1, "conditions": 1}


def sync_forward_lifecycle(forward) -> dict:
    instr = forward.instrument
    src_type, src_id = _src(instr)

    sched, _ = _get_or_create_schedule(
        name=f"Settlement date: {instr.reference}",
        kind=ScheduleKind.SETTLEMENT,
        source_type=src_type,
        source_id=src_id,
        starts_at=instr.created_at,
        frequency="once",
        next_run_at=forward.settlement_at,
    )
    cond, _ = _get_or_create_condition(
        name=f"Maturity reached: {instr.reference}",
        kind=ConditionKind.TIME,
        source_type=src_type,
        source_id=src_id,
        description="Forward contract maturity date reached.",
        expression={"type": "now_after", "datetime": forward.settlement_at.isoformat()},
    )
    _ensure_created_event(
        src_type, src_id,
        f"Forward created: {instr.reference}",
        schedule=sched,
    )
    return {"schedules": 1, "conditions": 1}


def sync_option_lifecycle(option) -> dict:
    instr = option.instrument
    src_type, src_id = _src(instr)

    ent, _ = _get_or_create_entitlement(
        holder_account=option.buyer_account,
        kind=EntitlementKind.EXERCISE_RIGHT,
        resource_label=f"Option exercise right: {instr.reference}",
        source_type=src_type,
        source_id=src_id,
        starts_at=instr.created_at,
        ends_at=option.expiry_at,
    )
    sched, _ = _get_or_create_schedule(
        name=f"Option expiry: {instr.reference}",
        kind=ScheduleKind.EXPIRY,
        source_type=src_type,
        source_id=src_id,
        starts_at=instr.created_at,
        frequency="once",
        next_run_at=option.expiry_at,
    )
    cond, _ = _get_or_create_condition(
        name=f"Before expiry: {instr.reference}",
        kind=ConditionKind.TIME,
        source_type=src_type,
        source_id=src_id,
        description="Option has not yet expired.",
        expression={"type": "now_before", "datetime": option.expiry_at.isoformat()},
    )
    _ensure_created_event(
        src_type, src_id,
        f"Option created: {instr.reference}",
        schedule=sched,
        entitlement=ent,
    )
    return {"entitlements": 1, "schedules": 1, "conditions": 1}


def sync_staking_lifecycle(staking) -> dict:
    instr = staking.instrument
    src_type, src_id = _src(instr)

    ent, _ = _get_or_create_entitlement(
        holder_account=staking.staker_account,
        kind=EntitlementKind.REWARD_ELIGIBILITY,
        resource_label=f"Staking reward eligibility: {instr.reference}",
        source_type=src_type,
        source_id=src_id,
        starts_at=instr.created_at,
        ends_at=staking.locked_until,
    )
    alloc, _ = _get_or_create_allocation(
        kind=AllocationKind.STAKING_LOCK,
        source_type=src_type,
        source_id=src_id,
        asset=staking.staked_asset,
        amount_base_units=staking.staked_amount_base_units,
        holder_account=staking.staking_account,
        beneficiary_account=staking.staker_account,
    )
    sched, _ = _get_or_create_schedule(
        name=f"Reward cycle: {instr.reference}",
        kind=ScheduleKind.REWARD,
        source_type=src_type,
        source_id=src_id,
        starts_at=instr.created_at,
        ends_at=staking.locked_until,
        frequency="monthly",
        next_run_at=staking.locked_until,
    )
    _ensure_created_event(
        src_type, src_id,
        f"Staking created: {instr.reference}",
        schedule=sched,
        allocation=alloc,
        entitlement=ent,
    )
    return {"entitlements": 1, "allocations": 1, "schedules": 1}


def sync_revenue_share_lifecycle(revenue_share) -> dict:
    instr = revenue_share.instrument
    src_type, src_id = _src(instr)

    recipients = list(revenue_share.recipients.select_related("account").all())
    ent_count = 0
    for recipient in recipients:
        _get_or_create_entitlement(
            holder_account=recipient.account,
            kind=EntitlementKind.CLAIM_RIGHT,
            resource_label=f"Revenue share claim: {instr.reference}",
            source_type=src_type,
            source_id=f"{src_id}:{recipient.account_id}",
        )
        ent_count += 1

    alloc, _ = _get_or_create_allocation(
        kind=AllocationKind.SPLIT,
        source_type=src_type,
        source_id=src_id,
        asset=revenue_share.revenue_asset,
        amount_base_units=1,
        holder_account=revenue_share.revenue_account,
        metadata={
            "conceptual": True,
            "recipients": [
                {"account_id": r.account_id, "share_bps": r.share_bps}
                for r in recipients
            ],
        },
    )
    active_from = revenue_share.active_from or instr.created_at
    sched, _ = _get_or_create_schedule(
        name=f"Payout cycle: {instr.reference}",
        kind=ScheduleKind.PAYOUT,
        source_type=src_type,
        source_id=src_id,
        starts_at=active_from,
        ends_at=revenue_share.active_until,
        frequency="monthly",
        next_run_at=active_from,
    )
    _ensure_created_event(
        src_type, src_id,
        f"Revenue share created: {instr.reference}",
        schedule=sched,
        allocation=alloc,
    )
    return {"entitlements": ent_count, "allocations": 1, "schedules": 1}
