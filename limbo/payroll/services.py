"""
Payroll v1 — contract archetype.

Payroll is modelled as a specialised contracts.Contract graph backed by
claims and assets primitives.  No new app, no new models.

Public API
----------
create_payroll_contract(...)  → Contract
create_payroll_duty(...)      → {"obligation": ..., "condition": ..., "event": ...}
mark_payroll_due(...)         → ContractEvent
approve_payroll_duty(...)     → ContractEvent
settle_payroll_duty(...)      → {"transaction": ..., "event": ...}

Constraints
-----------
* Never imports from kanban, academy, mobilization, response, or deployment.
* Settlement is always delegated to assets.services.assets.fulfill_obligation.
* Holdings are never mutated directly.
"""
from __future__ import annotations

import uuid as _uuid
from decimal import Decimal
from typing import Any

from django.db import transaction as db_transaction
from django.utils import timezone

from toto.contracts.models import Contract
from toto.contracts.services import create_edge, create_node

_SOURCE_TYPE = "contracts.Contract"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _from_base(amount_base_units: int, decimals: int) -> Decimal:
    factor = Decimal(10) ** decimals
    return Decimal(amount_base_units) / factor


# ---------------------------------------------------------------------------
# create_payroll_contract
# ---------------------------------------------------------------------------

def create_payroll_contract(
    *,
    name: str,
    payer_account,
    worker_person,
    worker_account,
    asset,
    amount_base_units: int,
    frequency: str,
    metadata: dict | None = None,
) -> Contract:
    """
    Build a payroll contract archetype.

    Creates:
      - contracts.Contract (draft)
      - claims.Schedule (PAYOUT, active)
      - claims.Allocation (BUDGET, active)
      - ContractNode/ContractEdge graph wiring all primitives together

    Parameters
    ----------
    name               : unique contract name
    payer_account      : assets.LedgerAccount — paying side
    worker_person      : people.Person — the employee
    worker_account     : assets.LedgerAccount — receiving side
    asset              : assets.Asset — currency of payment
    amount_base_units  : budget ceiling in base units
    frequency          : "monthly" / "weekly" / etc.
    metadata           : optional extra payload (may carry source_app, source_model, source_id)
    """
    from toto.claims.models import (
        Allocation, AllocationKind, AllocationStatus,
        Schedule, ScheduleKind, ScheduleStatus,
    )

    meta = dict(metadata or {})

    with db_transaction.atomic():
        contract = Contract.objects.create(
            name=name,
            description=(
                f"Payroll contract: {worker_person} paid by "
                f"{payer_account.code} in {asset.unit_name}."
            ),
            status=Contract.STATUS_DRAFT,
            metadata={**meta, "archetype": "payroll"},
        )

        schedule = Schedule.objects.create(
            name=f"{name} — pay schedule",
            kind=ScheduleKind.PAYOUT,
            status=ScheduleStatus.ACTIVE,
            frequency=frequency,
            starts_at=timezone.now(),
            next_run_at=timezone.now(),
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "payroll"},
        )
        allocation = Allocation.objects.create(
            kind=AllocationKind.BUDGET,
            status=AllocationStatus.ACTIVE,
            holder_account=payer_account,
            beneficiary_account=worker_account,
            asset=asset,
            amount_base_units=amount_base_units,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "payroll"},
        )

        # Nodes
        create_node(contract, "root", "contract",
                    title=name, object=contract)
        create_node(contract, "payer_acct", "ledger_account",
                    title=f"Payer: {payer_account.code}", object=payer_account)
        create_node(contract, "worker_person", "manual",
                    title=str(worker_person), object=worker_person)
        create_node(contract, "worker_acct", "ledger_account",
                    title=f"Worker: {worker_account.code}", object=worker_account)
        create_node(contract, "payment_asset", "asset",
                    title=asset.unit_name, object=asset)
        create_node(contract, "pay_schedule", "schedule",
                    title=schedule.name, object=schedule)
        create_node(contract, "budget", "allocation",
                    title=f"Budget {amount_base_units} {asset.unit_name}", object=allocation)

        # Edges
        create_edge(contract, "root", "pay_schedule", "creates_duty",
                    label="scheduled payout")
        create_edge(contract, "root", "budget", "allocates",
                    label="payroll budget")
        create_edge(contract, "root", "payer_acct", "debtor",
                    label="payer")
        create_edge(contract, "root", "worker_acct", "creditor",
                    label="payee")
        create_edge(contract, "root", "payment_asset", "uses_asset",
                    label="currency")
        create_edge(contract, "root", "worker_person", "related",
                    label="worker")

    return contract


# ---------------------------------------------------------------------------
# create_payroll_duty
# ---------------------------------------------------------------------------

def create_payroll_duty(
    *,
    contract: Contract,
    worker_person,
    worker_account,
    amount_base_units: int,
    due_at=None,
    schedule=None,
    metadata: dict | None = None,
) -> dict:
    """
    Create a single payroll duty under an existing payroll contract.

    Creates:
      - assets.Obligation (debtor=payer, creditor=worker)
      - claims.Condition (APPROVAL, pending)
      - claims.ContractEvent (OBLIGATION_CREATED)
      - ContractNode/ContractEdge entries

    Returns {"obligation": ..., "condition": ..., "event": ...}.
    """
    from toto.assets.services.assets import create_obligation
    from toto.claims.models import (
        Condition, ConditionKind, ConditionStatus,
        ContractEvent, ContractEventKind,
    )

    meta = dict(metadata or {})
    if due_at is None:
        due_at = timezone.now()

    payer_node = contract.nodes.get(key="payer_acct")
    asset_node = contract.nodes.get(key="payment_asset")
    payer_account = payer_node.get_object()
    asset = asset_node.get_object()

    with db_transaction.atomic():
        duty_index = contract.nodes.filter(node_type="obligation").count() + 1
        duty_key = f"duty_{duty_index}"
        approval_key = f"approval_{duty_index}"

        ref = f"payroll-{contract.pk}-duty-{duty_index}-{_uuid.uuid4().hex[:8]}"
        obligation = create_obligation(
            reference=ref,
            debtor_account=payer_account,
            creditor_account=worker_account,
            asset=asset,
            amount=_from_base(amount_base_units, asset.decimals),
            due_at=due_at,
            order_reference=f"payroll:{contract.pk}",
        )

        condition = Condition.objects.create(
            name=f"{contract.name} — approval {duty_index}",
            kind=ConditionKind.APPROVAL,
            status=ConditionStatus.PENDING,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "payroll", "duty_index": duty_index},
        )

        event = ContractEvent.objects.create(
            kind=ContractEventKind.OBLIGATION_CREATED,
            title=f"Payroll duty {duty_index} created for {worker_person}",
            obligation=obligation,
            condition=condition,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            payload={**meta, "archetype": "payroll", "duty_index": duty_index},
        )

        create_node(contract, duty_key, "obligation",
                    title=f"Duty {duty_index}: {amount_base_units} {asset.unit_name}",
                    object=obligation)
        create_node(contract, approval_key, "condition",
                    title=f"Approval {duty_index}",
                    object=condition)

        create_edge(contract, "root", duty_key, "creates_duty",
                    label=f"duty {duty_index}")
        create_edge(contract, approval_key, duty_key, "gates",
                    label="approval required")
        create_edge(contract, duty_key, "payer_acct", "debtor")
        create_edge(contract, duty_key, "worker_acct", "creditor")
        create_edge(contract, duty_key, "payment_asset", "uses_asset")

    return {"obligation": obligation, "condition": condition, "event": event}


# ---------------------------------------------------------------------------
# mark_payroll_due
# ---------------------------------------------------------------------------

def mark_payroll_due(
    *,
    contract: Contract,
    duty_node_key: str,
    metadata: dict | None = None,
) -> Any:
    """
    Record that a payroll duty is now due.  Creates a PAYMENT_DUE ContractEvent.
    """
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    obligation_node = contract.nodes.get(key=duty_node_key)
    obligation = obligation_node.get_object()

    return ContractEvent.objects.create(
        kind=ContractEventKind.PAYMENT_DUE,
        title=f"Payroll due: {obligation.reference}",
        obligation=obligation,
        source_type=_SOURCE_TYPE,
        source_id=str(contract.pk),
        payload={**meta, "archetype": "payroll"},
    )


# ---------------------------------------------------------------------------
# approve_payroll_duty
# ---------------------------------------------------------------------------

def approve_payroll_duty(
    *,
    condition,
    approver=None,
    metadata: dict | None = None,
) -> Any:
    """
    Satisfy the approval condition for a payroll duty.

    Marks the Condition as satisfied and creates a CONDITION_SATISFIED event.
    """
    from toto.claims.models import (
        ConditionStatus,
        ContractEvent, ContractEventKind,
    )

    meta = dict(metadata or {})
    with db_transaction.atomic():
        condition.status = ConditionStatus.SATISFIED
        condition.satisfied_at = timezone.now()
        condition.save(update_fields=["status", "satisfied_at", "updated_at"])

        event = ContractEvent.objects.create(
            kind=ContractEventKind.CONDITION_SATISFIED,
            title=f"Payroll approved: {condition.name}",
            condition=condition,
            actor=approver,
            source_type=condition.source_type,
            source_id=condition.source_id,
            payload={**meta, "archetype": "payroll"},
        )
    return event


# ---------------------------------------------------------------------------
# settle_payroll_duty
# ---------------------------------------------------------------------------

def settle_payroll_duty(
    *,
    obligation,
    contract: Contract | None = None,
    reference: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Fulfill a payroll obligation via the assets backend.

    Delegates entirely to assets.services.assets.fulfill_obligation —
    holdings are never mutated directly here.

    Returns {"transaction": LedgerTransaction, "event": ContractEvent}.
    """
    from toto.assets.services.assets import fulfill_obligation
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    ref = reference or f"payroll-settle-{obligation.reference}"

    source_id = str(contract.pk) if contract else obligation.order_reference

    with db_transaction.atomic():
        tx = fulfill_obligation(
            obligation=obligation,
            reference=ref,
            description=f"Payroll settlement: {obligation.reference}",
        )
        event = ContractEvent.objects.create(
            kind=ContractEventKind.SETTLEMENT,
            title=f"Payroll settled: {obligation.reference}",
            obligation=obligation,
            transaction=tx,
            source_type=_SOURCE_TYPE,
            source_id=source_id,
            payload={**meta, "archetype": "payroll", "reference": ref},
        )
    return {"transaction": tx, "event": event}
