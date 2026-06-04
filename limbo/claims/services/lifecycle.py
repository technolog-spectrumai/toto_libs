"""
Lifecycle service helpers for Schedule, Condition, Allocation, and ContractEvent.

These functions are documentation/audit helpers only.
They do not mutate ledger balances.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone

from toto.claims.models import (
    Allocation,
    AllocationStatus,
    Condition,
    ConditionStatus,
    ContractEvent,
    ContractEventKind,
    Entitlement,
    EntitlementStatus,
    Schedule,
    ScheduleStatus,
)


# ---------------------------------------------------------------------------
# ContractEvent factory
# ---------------------------------------------------------------------------

def create_event(
    *,
    kind: str,
    title: str,
    description: str = "",
    agreement=None,
    contract=None,
    actor=None,
    transaction=None,
    obligation=None,
    entitlement=None,
    schedule=None,
    condition=None,
    allocation=None,
    source_type: str = "",
    source_id: str = "",
    payload: dict | None = None,
) -> ContractEvent:
    return ContractEvent.objects.create(
        kind=kind,
        title=title,
        description=description,
        agreement=agreement,
        contract=contract,
        actor=actor,
        transaction=transaction,
        obligation=obligation,
        entitlement=entitlement,
        schedule=schedule,
        condition=condition,
        allocation=allocation,
        source_type=source_type,
        source_id=str(source_id) if source_id else "",
        payload=payload or {},
    )


def create_obligation_event(
    obligation, kind: str = ContractEventKind.OBLIGATION_CREATED
) -> ContractEvent:
    return create_event(
        kind=kind,
        title=f"Obligation {obligation.reference}",
        obligation=obligation,
        source_type="assets.Obligation",
        source_id=str(obligation.pk),
    )


def grant_entitlement_event(entitlement) -> ContractEvent:
    return create_event(
        kind=ContractEventKind.ENTITLEMENT_GRANTED,
        title=f"Entitlement granted: {entitlement.resource_label}",
        entitlement=entitlement,
        source_type="claims.Entitlement",
        source_id=str(entitlement.pk),
    )


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def schedule_due_events(now=None) -> list[ContractEvent]:
    if now is None:
        now = timezone.now()
    due = Schedule.objects.due(now=now)
    return [
        create_event(
            kind=ContractEventKind.CUSTOM,
            title=f"Schedule due: {sched.name}",
            schedule=sched,
            source_type=sched.source_type,
            source_id=sched.source_id,
            payload={"schedule_kind": sched.kind, "run_at": now.isoformat()},
        )
        for sched in due
    ]


def process_due_schedules(now=None, dry_run: bool = False) -> list[dict]:
    if now is None:
        now = timezone.now()
    due = list(Schedule.objects.due(now=now))
    results = []
    for sched in due:
        if not dry_run:
            sched.last_run_at = now
            sched.save(update_fields=["last_run_at", "updated_at"])
        results.append({"schedule_id": sched.pk, "name": sched.name, "dry_run": dry_run})
    return results


# ---------------------------------------------------------------------------
# Condition helpers
# ---------------------------------------------------------------------------

def mark_condition_satisfied(condition) -> None:
    condition.status = ConditionStatus.SATISFIED
    condition.satisfied_at = timezone.now()
    condition.save(update_fields=["status", "satisfied_at", "updated_at"])


def mark_condition_failed(condition, reason: str = "") -> None:
    condition.status = ConditionStatus.FAILED
    condition.failed_at = timezone.now()
    if reason:
        condition.metadata = {**(condition.metadata or {}), "failure_reason": reason}
    condition.save(update_fields=["status", "failed_at", "metadata", "updated_at"])


def waive_condition(condition, reason: str = "") -> None:
    condition.status = ConditionStatus.WAIVED
    if reason:
        condition.metadata = {**(condition.metadata or {}), "waive_reason": reason}
    condition.save(update_fields=["status", "metadata", "updated_at"])


def evaluate_condition(condition, context: dict | None = None, strict: bool = False) -> bool:
    """
    Evaluate a lightweight condition expression.

    Supported types: always_true, status_equals, now_after, now_before, balance_at_least.
    Returns False for unsupported types unless strict=True.
    """
    expr = condition.expression or {}
    expr_type = expr.get("type")

    if expr_type == "always_true":
        return True

    if expr_type == "status_equals":
        return (context or {}).get("status") == expr.get("value")

    if expr_type == "now_after":
        target = expr.get("datetime")
        if not target:
            return False
        import datetime
        dt = datetime.datetime.fromisoformat(target)
        if dt.tzinfo is None:
            from django.utils.timezone import make_aware
            dt = make_aware(dt)
        return timezone.now() > dt

    if expr_type == "now_before":
        target = expr.get("datetime")
        if not target:
            return False
        import datetime
        dt = datetime.datetime.fromisoformat(target)
        if dt.tzinfo is None:
            from django.utils.timezone import make_aware
            dt = make_aware(dt)
        return timezone.now() < dt

    if expr_type == "balance_at_least":
        balance = (context or {}).get("balance", 0)
        return balance >= expr.get("amount", 0)

    if strict:
        raise ValidationError(f"Unsupported condition expression type: {expr_type!r}")
    return False


def evaluate_conditions_for_source(source_type: str, source_id) -> list[dict]:
    conditions = Condition.objects.filter(
        source_type=source_type,
        source_id=str(source_id),
        status=ConditionStatus.PENDING,
    )
    return [
        {"condition_id": c.pk, "name": c.name, "result": evaluate_condition(c)}
        for c in conditions
    ]


# ---------------------------------------------------------------------------
# Allocation helpers
# ---------------------------------------------------------------------------

def activate_allocation(allocation) -> None:
    allocation.status = AllocationStatus.ACTIVE
    allocation.allocated_amount_base_units = allocation.amount_base_units
    allocation.save(update_fields=["status", "allocated_amount_base_units", "updated_at"])


def release_allocation(allocation, amount: int, transaction=None) -> None:
    allocation.released_amount_base_units += amount
    if (
        allocation.released_amount_base_units + allocation.consumed_amount_base_units
        >= allocation.amount_base_units
    ):
        allocation.status = AllocationStatus.RELEASED
    allocation.save(update_fields=["released_amount_base_units", "status", "updated_at"])


def consume_allocation(allocation, amount: int, transaction=None) -> None:
    allocation.consumed_amount_base_units += amount
    if (
        allocation.released_amount_base_units + allocation.consumed_amount_base_units
        >= allocation.amount_base_units
    ):
        allocation.status = AllocationStatus.CONSUMED
    allocation.save(update_fields=["consumed_amount_base_units", "status", "updated_at"])


def cancel_allocation(allocation) -> None:
    allocation.status = AllocationStatus.CANCELLED
    allocation.save(update_fields=["status", "updated_at"])


# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------

def create_checkpoint(source_type: str, source_id, data: dict) -> ContractEvent:
    return create_event(
        kind=ContractEventKind.CUSTOM,
        title=f"Checkpoint: {source_type}/{source_id}",
        source_type=source_type,
        source_id=str(source_id),
        payload={"checkpoint": True, **data},
    )


# ---------------------------------------------------------------------------
# Entitlement helpers
# ---------------------------------------------------------------------------

_ENTITLEMENT_TRANSITIONS = {
    EntitlementStatus.ACTIVE:    {EntitlementStatus.REVOKED, EntitlementStatus.SUSPENDED},
    EntitlementStatus.SUSPENDED: {EntitlementStatus.ACTIVE, EntitlementStatus.REVOKED},
}


def _check_entitlement_transition(entitlement, target: str) -> None:
    allowed = _ENTITLEMENT_TRANSITIONS.get(entitlement.status, set())
    if target not in allowed:
        raise ValidationError(
            f"Cannot transition entitlement from {entitlement.status!r} to {target!r}."
        )


def revoke_entitlement(entitlement, actor=None, reason: str = "") -> None:
    _check_entitlement_transition(entitlement, EntitlementStatus.REVOKED)
    entitlement.status = EntitlementStatus.REVOKED
    entitlement.save(update_fields=["status", "updated_at"])
    create_event(
        kind=ContractEventKind.ENTITLEMENT_REVOKED,
        title=f"Entitlement revoked: {entitlement.resource_label}",
        entitlement=entitlement,
        actor=actor,
        source_type="claims.Entitlement",
        source_id=str(entitlement.pk),
        payload={"reason": reason} if reason else {},
    )


def suspend_entitlement(entitlement, actor=None, reason: str = "") -> None:
    _check_entitlement_transition(entitlement, EntitlementStatus.SUSPENDED)
    entitlement.status = EntitlementStatus.SUSPENDED
    entitlement.save(update_fields=["status", "updated_at"])
    create_event(
        kind=ContractEventKind.CUSTOM,
        title=f"Entitlement suspended: {entitlement.resource_label}",
        entitlement=entitlement,
        actor=actor,
        source_type="claims.Entitlement",
        source_id=str(entitlement.pk),
        payload={"reason": reason} if reason else {},
    )


def reactivate_entitlement(entitlement, actor=None) -> None:
    _check_entitlement_transition(entitlement, EntitlementStatus.ACTIVE)
    entitlement.status = EntitlementStatus.ACTIVE
    entitlement.save(update_fields=["status", "updated_at"])
    create_event(
        kind=ContractEventKind.ENTITLEMENT_GRANTED,
        title=f"Entitlement reactivated: {entitlement.resource_label}",
        entitlement=entitlement,
        actor=actor,
        source_type="claims.Entitlement",
        source_id=str(entitlement.pk),
    )


# ---------------------------------------------------------------------------
# Schedule helpers (extended)
# ---------------------------------------------------------------------------

_SCHEDULE_TRANSITIONS = {
    ScheduleStatus.DRAFT:  {ScheduleStatus.ACTIVE, ScheduleStatus.CANCELLED},
    ScheduleStatus.ACTIVE: {ScheduleStatus.PAUSED, ScheduleStatus.COMPLETED, ScheduleStatus.CANCELLED},
    ScheduleStatus.PAUSED: {ScheduleStatus.ACTIVE, ScheduleStatus.CANCELLED},
}


def _check_schedule_transition(schedule, target: str) -> None:
    allowed = _SCHEDULE_TRANSITIONS.get(schedule.status, set())
    if target not in allowed:
        raise ValidationError(
            f"Cannot transition schedule from {schedule.status!r} to {target!r}."
        )


def _schedule_event_kind(target: str) -> str:
    return {
        ScheduleStatus.ACTIVE:    ContractEventKind.ACTIVATED,
        ScheduleStatus.PAUSED:    ContractEventKind.PAUSED,
        ScheduleStatus.COMPLETED: ContractEventKind.CUSTOM,
        ScheduleStatus.CANCELLED: ContractEventKind.CANCELLED,
    }.get(target, ContractEventKind.CUSTOM)


def transition_schedule(schedule, target: str, actor=None, reason: str = "") -> None:
    _check_schedule_transition(schedule, target)
    schedule.status = target
    schedule.save(update_fields=["status", "updated_at"])
    create_event(
        kind=_schedule_event_kind(target),
        title=f"Schedule {target}: {schedule.name}",
        schedule=schedule,
        actor=actor,
        source_type="claims.Schedule",
        source_id=str(schedule.pk),
        payload={"reason": reason} if reason else {},
    )


# ---------------------------------------------------------------------------
# Condition helpers (extended)
# ---------------------------------------------------------------------------

def cancel_condition(condition, actor=None, reason: str = "") -> None:
    if condition.status != "pending":
        raise ValidationError("Can only cancel a pending condition.")
    condition.status = ConditionStatus.CANCELLED
    if reason:
        condition.metadata = {**(condition.metadata or {}), "cancel_reason": reason}
    condition.save(update_fields=["status", "metadata", "updated_at"])
    create_event(
        kind=ContractEventKind.CANCELLED,
        title=f"Condition cancelled: {condition.name}",
        condition=condition,
        actor=actor,
        source_type="claims.Condition",
        source_id=str(condition.pk),
        payload={"reason": reason} if reason else {},
    )


def _emit_condition_event(condition, kind: str, actor=None, payload: dict | None = None) -> None:
    create_event(
        kind=kind,
        title=f"Condition {condition.status}: {condition.name}",
        condition=condition,
        actor=actor,
        source_type="claims.Condition",
        source_id=str(condition.pk),
        payload=payload or {},
    )


# ---------------------------------------------------------------------------
# Allocation helpers (extended)
# ---------------------------------------------------------------------------

_ALLOCATION_TRANSITIONS = {
    AllocationStatus.DRAFT:  {AllocationStatus.ACTIVE, AllocationStatus.CANCELLED},
    AllocationStatus.ACTIVE: {AllocationStatus.RELEASED, AllocationStatus.CONSUMED, AllocationStatus.CANCELLED},
}


def _check_allocation_transition(allocation, target: str) -> None:
    allowed = _ALLOCATION_TRANSITIONS.get(allocation.status, set())
    if target not in allowed:
        raise ValidationError(
            f"Cannot transition allocation from {allocation.status!r} to {target!r}."
        )
