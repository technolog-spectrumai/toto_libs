"""
Loans v1 — contract archetype.

Models a same-currency loan as a contracts.Contract graph backed by
claims and assets primitives.  No new app, no new models.

Public API
----------
create_loan_contract(...)     → Contract
create_loan_disbursement(...) → {"obligation": ..., "event": ...}
create_repayment_duty(...)    → {"obligation": ..., "condition": ..., "event": ...}
mark_repayment_due(...)       → ContractEvent
approve_repayment(...)        → ContractEvent
settle_repayment(...)         → {"transaction": ..., "event": ...}

Constraints
-----------
* Never imports from kanban, academy, mobilization, response, or deployment.
* Settlement always delegates to assets.services.assets.fulfill_obligation.
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

REPAYMENT_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("biannual", "Bi-annual"),
    ("yearly", "Yearly"),
    ("bullet", "Bullet (at maturity)"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _from_base(amount_base_units: int, decimals: int) -> Decimal:
    return Decimal(amount_base_units) / (Decimal(10) ** decimals)


# ---------------------------------------------------------------------------
# create_loan_contract
# ---------------------------------------------------------------------------

def create_loan_contract(
    *,
    name: str,
    lender_person,
    borrower_person,
    lender_account,
    borrower_account,
    loan_asset,
    principal_base_units: int,
    interest_rate_bps: int,
    repayment_frequency: str,
    term_months: int,
    metadata: dict | None = None,
) -> Contract:
    """
    Build a same-currency loan contract archetype.

    Parameters
    ----------
    name                  : unique contract name
    lender_person         : people.Person — the lender
    borrower_person       : people.Person — the borrower
    lender_account        : assets.LedgerAccount — disbursing account
    borrower_account      : assets.LedgerAccount — receiving / repayment account
    loan_asset            : assets.Asset — single currency (same for both sides)
    principal_base_units  : principal amount in base units
    interest_rate_bps     : annual interest rate in basis points (e.g. 500 = 5 %)
    repayment_frequency   : "monthly" / "quarterly" / "yearly" / "bullet"
    term_months           : loan term in months
    metadata              : optional extra payload
    """
    meta = dict(metadata or {})
    interest_pct = interest_rate_bps / 100

    with db_transaction.atomic():
        contract = Contract.objects.create(
            name=name,
            description=(
                f"Loan from {lender_person} to {borrower_person}. "
                f"Principal: {principal_base_units} {loan_asset.unit_name}. "
                f"Interest: {interest_pct:.2f}% p.a. Term: {term_months} months."
            ),
            status=Contract.STATUS_DRAFT,
            metadata={
                **meta,
                "archetype": "loan",
                "loan_asset_unit": loan_asset.unit_name,
                "principal_base_units": principal_base_units,
                "interest_rate_bps": interest_rate_bps,
                "repayment_frequency": repayment_frequency,
                "term_months": term_months,
            },
        )

        create_node(contract, "root", "contract",
                    title=name, object=contract)
        create_node(contract, "lender_person", "manual",
                    title=str(lender_person), object=lender_person)
        create_node(contract, "borrower_person", "manual",
                    title=str(borrower_person), object=borrower_person)
        create_node(contract, "lender_acct", "ledger_account",
                    title=f"Lender: {lender_account.code}", object=lender_account)
        create_node(contract, "borrower_acct", "ledger_account",
                    title=f"Borrower: {borrower_account.code}", object=borrower_account)
        create_node(contract, "loan_asset", "asset",
                    title=loan_asset.unit_name, object=loan_asset)

        create_edge(contract, "root", "lender_person", "creditor",
                    label="lender")
        create_edge(contract, "root", "borrower_person", "debtor",
                    label="borrower")
        create_edge(contract, "root", "lender_acct", "creditor",
                    label="disbursement account")
        create_edge(contract, "root", "borrower_acct", "debtor",
                    label="repayment account")
        create_edge(contract, "root", "loan_asset", "uses_asset",
                    label="loan currency")

    return contract


# ---------------------------------------------------------------------------
# create_loan_disbursement
# ---------------------------------------------------------------------------

def create_loan_disbursement(
    *,
    contract: Contract,
    amount_base_units: int,
    due_at=None,
    metadata: dict | None = None,
) -> dict:
    """
    Create the principal disbursement obligation for a loan contract.

    Debtor = lender account, Creditor = borrower account.

    Creates:
      - assets.Obligation (lender → borrower)
      - claims.ContractEvent (OBLIGATION_CREATED)
      - ContractNode/ContractEdge entries

    Returns {"obligation": ..., "event": ...}.
    """
    from toto.assets.services.assets import create_obligation
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    if due_at is None:
        due_at = timezone.now()

    lender_node = contract.nodes.get(key="lender_acct")
    borrower_node = contract.nodes.get(key="borrower_acct")
    asset_node = contract.nodes.get(key="loan_asset")
    lender_account = lender_node.get_object()
    borrower_account = borrower_node.get_object()
    asset = asset_node.get_object()

    with db_transaction.atomic():
        index = contract.nodes.filter(node_type="obligation", key__startswith="disbursement_").count() + 1
        node_key = f"disbursement_{index}"

        ref = f"loan-{contract.pk}-disburse-{index}-{_uuid.uuid4().hex[:8]}"
        obligation = create_obligation(
            reference=ref,
            debtor_account=lender_account,
            creditor_account=borrower_account,
            asset=asset,
            amount=_from_base(amount_base_units, asset.decimals),
            due_at=due_at,
            order_reference=f"loan:{contract.pk}",
        )

        event = ContractEvent.objects.create(
            kind=ContractEventKind.OBLIGATION_CREATED,
            title=f"Loan disbursement {index}: {amount_base_units} {asset.unit_name}",
            obligation=obligation,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            payload={**meta, "archetype": "loan", "disbursement_index": index},
        )

        create_node(contract, node_key, "obligation",
                    title=f"Disbursement {index}: {amount_base_units} {asset.unit_name}",
                    object=obligation)
        create_edge(contract, "root", node_key, "disburses",
                    label=f"disbursement {index}")
        create_edge(contract, node_key, "lender_acct", "debtor")
        create_edge(contract, node_key, "borrower_acct", "creditor")
        create_edge(contract, node_key, "loan_asset", "uses_asset")

    return {"obligation": obligation, "event": event}


# ---------------------------------------------------------------------------
# create_repayment_duty
# ---------------------------------------------------------------------------

def create_repayment_duty(
    *,
    contract: Contract,
    amount_base_units: int,
    due_at=None,
    metadata: dict | None = None,
) -> dict:
    """
    Create a single repayment duty under an existing loan contract.

    Debtor = borrower account, Creditor = lender account.

    Creates:
      - assets.Obligation (borrower → lender)
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

    borrower_node = contract.nodes.get(key="borrower_acct")
    lender_node = contract.nodes.get(key="lender_acct")
    asset_node = contract.nodes.get(key="loan_asset")
    borrower_account = borrower_node.get_object()
    lender_account = lender_node.get_object()
    asset = asset_node.get_object()

    with db_transaction.atomic():
        index = contract.nodes.filter(node_type="obligation", key__startswith="repayment_").count() + 1
        duty_key = f"repayment_{index}"
        approval_key = f"repayment_approval_{index}"

        ref = f"loan-{contract.pk}-repay-{index}-{_uuid.uuid4().hex[:8]}"
        obligation = create_obligation(
            reference=ref,
            debtor_account=borrower_account,
            creditor_account=lender_account,
            asset=asset,
            amount=_from_base(amount_base_units, asset.decimals),
            due_at=due_at,
            order_reference=f"loan:{contract.pk}",
        )

        condition = Condition.objects.create(
            name=f"{contract.name} — repayment approval {index}",
            kind=ConditionKind.APPROVAL,
            status=ConditionStatus.PENDING,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "loan", "repayment_index": index},
        )

        event = ContractEvent.objects.create(
            kind=ContractEventKind.OBLIGATION_CREATED,
            title=f"Loan repayment {index}: {amount_base_units} {asset.unit_name}",
            obligation=obligation,
            condition=condition,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            payload={**meta, "archetype": "loan", "repayment_index": index},
        )

        create_node(contract, duty_key, "obligation",
                    title=f"Repayment {index}: {amount_base_units} {asset.unit_name}",
                    object=obligation)
        create_node(contract, approval_key, "condition",
                    title=f"Repayment approval {index}",
                    object=condition)

        create_edge(contract, "root", duty_key, "creates_duty",
                    label=f"repayment {index}")
        create_edge(contract, approval_key, duty_key, "gates",
                    label="approval required")
        create_edge(contract, duty_key, "borrower_acct", "debtor")
        create_edge(contract, duty_key, "lender_acct", "creditor")
        create_edge(contract, duty_key, "loan_asset", "uses_asset")

    return {"obligation": obligation, "condition": condition, "event": event}


# ---------------------------------------------------------------------------
# mark_repayment_due
# ---------------------------------------------------------------------------

def mark_repayment_due(
    *,
    contract: Contract,
    duty_node_key: str,
    metadata: dict | None = None,
) -> Any:
    """
    Record that a loan repayment is now due.  Creates a PAYMENT_DUE ContractEvent.
    """
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    obligation_node = contract.nodes.get(key=duty_node_key)
    obligation = obligation_node.get_object()

    return ContractEvent.objects.create(
        kind=ContractEventKind.PAYMENT_DUE,
        title=f"Loan repayment due: {obligation.reference}",
        obligation=obligation,
        source_type=_SOURCE_TYPE,
        source_id=str(contract.pk),
        payload={**meta, "archetype": "loan"},
    )


# ---------------------------------------------------------------------------
# approve_repayment
# ---------------------------------------------------------------------------

def approve_repayment(
    *,
    condition,
    approver=None,
    metadata: dict | None = None,
) -> Any:
    """
    Satisfy the approval condition for a loan repayment.

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
            title=f"Loan repayment approved: {condition.name}",
            condition=condition,
            actor=approver,
            source_type=condition.source_type,
            source_id=condition.source_id,
            payload={**meta, "archetype": "loan"},
        )
    return event


# ---------------------------------------------------------------------------
# settle_repayment
# ---------------------------------------------------------------------------

def settle_repayment(
    *,
    obligation,
    contract: Contract | None = None,
    reference: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Fulfill a loan repayment obligation via the assets backend.

    Delegates entirely to assets.services.assets.fulfill_obligation —
    holdings are never mutated directly here.

    Returns {"transaction": LedgerTransaction, "event": ContractEvent}.
    """
    from toto.assets.services.assets import fulfill_obligation
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    ref = reference or f"loan-settle-{obligation.reference}"
    source_id = str(contract.pk) if contract else obligation.order_reference

    with db_transaction.atomic():
        tx = fulfill_obligation(
            obligation=obligation,
            reference=ref,
            description=f"Loan repayment settlement: {obligation.reference}",
        )
        event = ContractEvent.objects.create(
            kind=ContractEventKind.SETTLEMENT,
            title=f"Loan repayment settled: {obligation.reference}",
            obligation=obligation,
            transaction=tx,
            source_type=_SOURCE_TYPE,
            source_id=source_id,
            payload={**meta, "archetype": "loan", "reference": ref},
        )
    return {"transaction": tx, "event": event}
