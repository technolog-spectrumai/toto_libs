"""
Insurance v1 — contract archetype.

Models an asset insurance policy as a contracts.Contract graph backed by
claims and assets primitives.  No new app, no new models.

Public API
----------
create_insurance_contract(...)  → Contract
create_premium_duty(...)        → {"obligation": ..., "condition": ..., "event": ...}
mark_premium_due(...)           → ContractEvent
approve_premium(...)            → ContractEvent
settle_premium(...)             → {"transaction": ..., "event": ...}
create_claim_duty(...)          → {"obligation": ..., "condition": ..., "event": ...}
approve_claim(...)              → ContractEvent
settle_claim(...)               → {"transaction": ..., "event": ...}

Constraints
-----------
* Never imports from kanban, academy, mobilization, response, bazaar, or deployment.
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

PREMIUM_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
    ("biannual", "Bi-annual"),
    ("weekly", "Weekly"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _from_base(amount_base_units: int, decimals: int) -> Decimal:
    return Decimal(amount_base_units) / (Decimal(10) ** decimals)


# ---------------------------------------------------------------------------
# create_insurance_contract
# ---------------------------------------------------------------------------

def create_insurance_contract(
    *,
    name: str,
    insured_person,
    payer_account,
    insurer_account,
    premium_asset,
    premium_amount_base_units: int,
    premium_frequency: str,
    insured_items: list,
    insured_financial_assets: list,
    metadata: dict | None = None,
) -> Contract:
    """
    Build an insurance contract archetype.

    Parameters
    ----------
    name                        : unique contract name
    insured_person              : people.Person — the policy holder
    payer_account               : assets.LedgerAccount — pays premiums
    insurer_account             : assets.LedgerAccount — receives premiums
    premium_asset               : assets.Asset — currency of premium
    premium_amount_base_units   : premium amount in base units
    premium_frequency           : "monthly" / "quarterly" / "yearly" / …
    insured_items               : list of inventory.RealWorldObject to cover
    insured_financial_assets    : list of assets.Asset to cover
    metadata                    : optional extra payload
    """
    meta = dict(metadata or {})

    with db_transaction.atomic():
        total_inventory_value = sum(
            int(item.estimated_value or 0) for item in insured_items
        )
        total_covered = len(insured_items) + len(insured_financial_assets)

        contract = Contract.objects.create(
            name=name,
            description=(
                f"Insurance policy for {insured_person} covering "
                f"{total_covered} asset(s). "
                f"Premium: {premium_amount_base_units} {premium_asset.unit_name} / {premium_frequency}."
            ),
            status=Contract.STATUS_DRAFT,
            metadata={
                **meta,
                "archetype": "insurance",
                "premium_frequency": premium_frequency,
                "premium_amount_base_units": premium_amount_base_units,
                "insured_item_count": len(insured_items),
                "insured_financial_asset_count": len(insured_financial_assets),
                "total_estimated_value": total_inventory_value,
            },
        )

        # Core nodes
        create_node(contract, "root", "contract",
                    title=name, object=contract)
        create_node(contract, "insured_person", "manual",
                    title=str(insured_person), object=insured_person)
        create_node(contract, "payer_acct", "ledger_account",
                    title=f"Payer: {payer_account.code}", object=payer_account)
        create_node(contract, "insurer_acct", "ledger_account",
                    title=f"Insurer: {insurer_account.code}", object=insurer_account)
        create_node(contract, "premium_asset", "asset",
                    title=premium_asset.unit_name, object=premium_asset)

        # Inventory asset nodes
        for i, item in enumerate(insured_items, start=1):
            create_node(contract, f"insured_item_{i}", "manual",
                        title=item.name, object=item)
            create_edge(contract, "root", f"insured_item_{i}", "insures",
                        label=f"inventory asset {i}")

        # Financial asset nodes
        for i, asset in enumerate(insured_financial_assets, start=1):
            create_node(contract, f"insured_fasset_{i}", "asset",
                        title=asset.unit_name, object=asset)
            create_edge(contract, "root", f"insured_fasset_{i}", "insures",
                        label=f"financial asset {i}")

        # Core edges
        create_edge(contract, "root", "insured_person", "covers",
                    label="policy holder")
        create_edge(contract, "root", "payer_acct", "debtor",
                    label="premium payer")
        create_edge(contract, "root", "insurer_acct", "creditor",
                    label="insurer")
        create_edge(contract, "root", "premium_asset", "uses_asset",
                    label="premium currency")

    return contract


# ---------------------------------------------------------------------------
# create_premium_duty
# ---------------------------------------------------------------------------

def create_premium_duty(
    *,
    contract: Contract,
    amount_base_units: int,
    due_at=None,
    metadata: dict | None = None,
) -> dict:
    """
    Create a single premium payment duty under an existing insurance contract.

    Debtor = payer account (insured), Creditor = insurer account.

    Creates:
      - assets.Obligation (payer → insurer)
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
    insurer_node = contract.nodes.get(key="insurer_acct")
    asset_node = contract.nodes.get(key="premium_asset")
    payer_account = payer_node.get_object()
    insurer_account = insurer_node.get_object()
    asset = asset_node.get_object()

    with db_transaction.atomic():
        index = contract.nodes.filter(node_type="obligation", key__startswith="premium_").count() + 1
        duty_key = f"premium_{index}"
        approval_key = f"premium_approval_{index}"

        ref = f"insurance-{contract.pk}-premium-{index}-{_uuid.uuid4().hex[:8]}"
        obligation = create_obligation(
            reference=ref,
            debtor_account=payer_account,
            creditor_account=insurer_account,
            asset=asset,
            amount=_from_base(amount_base_units, asset.decimals),
            due_at=due_at,
            order_reference=f"insurance:{contract.pk}",
        )

        condition = Condition.objects.create(
            name=f"{contract.name} — premium approval {index}",
            kind=ConditionKind.APPROVAL,
            status=ConditionStatus.PENDING,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "insurance", "premium_index": index},
        )

        event = ContractEvent.objects.create(
            kind=ContractEventKind.OBLIGATION_CREATED,
            title=f"Insurance premium {index}: {amount_base_units} {asset.unit_name}",
            obligation=obligation,
            condition=condition,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            payload={**meta, "archetype": "insurance", "premium_index": index},
        )

        create_node(contract, duty_key, "obligation",
                    title=f"Premium {index}: {amount_base_units} {asset.unit_name}",
                    object=obligation)
        create_node(contract, approval_key, "condition",
                    title=f"Premium approval {index}",
                    object=condition)

        create_edge(contract, "root", duty_key, "creates_duty",
                    label=f"premium {index}")
        create_edge(contract, approval_key, duty_key, "gates",
                    label="approval required")
        create_edge(contract, duty_key, "payer_acct", "debtor")
        create_edge(contract, duty_key, "insurer_acct", "creditor")
        create_edge(contract, duty_key, "premium_asset", "uses_asset")

    return {"obligation": obligation, "condition": condition, "event": event}


# ---------------------------------------------------------------------------
# mark_premium_due
# ---------------------------------------------------------------------------

def mark_premium_due(
    *,
    contract: Contract,
    duty_node_key: str,
    metadata: dict | None = None,
) -> Any:
    """
    Record that an insurance premium is now due.  Creates a PAYMENT_DUE ContractEvent.
    """
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    obligation_node = contract.nodes.get(key=duty_node_key)
    obligation = obligation_node.get_object()

    return ContractEvent.objects.create(
        kind=ContractEventKind.PAYMENT_DUE,
        title=f"Insurance premium due: {obligation.reference}",
        obligation=obligation,
        source_type=_SOURCE_TYPE,
        source_id=str(contract.pk),
        payload={**meta, "archetype": "insurance"},
    )


# ---------------------------------------------------------------------------
# approve_premium
# ---------------------------------------------------------------------------

def approve_premium(
    *,
    condition,
    approver=None,
    metadata: dict | None = None,
) -> Any:
    """
    Satisfy the approval condition for an insurance premium.

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
            title=f"Insurance premium approved: {condition.name}",
            condition=condition,
            actor=approver,
            source_type=condition.source_type,
            source_id=condition.source_id,
            payload={**meta, "archetype": "insurance"},
        )
    return event


# ---------------------------------------------------------------------------
# settle_premium
# ---------------------------------------------------------------------------

def settle_premium(
    *,
    obligation,
    contract: Contract | None = None,
    reference: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Fulfill an insurance premium obligation via the assets backend.

    Delegates entirely to assets.services.assets.fulfill_obligation —
    holdings are never mutated directly here.

    Returns {"transaction": LedgerTransaction, "event": ContractEvent}.
    """
    from toto.assets.services.assets import fulfill_obligation
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    ref = reference or f"insurance-premium-settle-{obligation.reference}"
    source_id = str(contract.pk) if contract else obligation.order_reference

    with db_transaction.atomic():
        tx = fulfill_obligation(
            obligation=obligation,
            reference=ref,
            description=f"Insurance premium settlement: {obligation.reference}",
        )
        event = ContractEvent.objects.create(
            kind=ContractEventKind.SETTLEMENT,
            title=f"Insurance premium settled: {obligation.reference}",
            obligation=obligation,
            transaction=tx,
            source_type=_SOURCE_TYPE,
            source_id=source_id,
            payload={**meta, "archetype": "insurance", "reference": ref},
        )
    return {"transaction": tx, "event": event}


# ---------------------------------------------------------------------------
# create_claim_duty
# ---------------------------------------------------------------------------

def create_claim_duty(
    *,
    contract: Contract,
    amount_base_units: int,
    due_at=None,
    metadata: dict | None = None,
) -> dict:
    """
    Create a claim payout duty under an existing insurance contract.

    Debtor = insurer account, Creditor = payer (insured) account.

    Creates:
      - assets.Obligation (insurer → insured)
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

    insurer_node = contract.nodes.get(key="insurer_acct")
    payer_node = contract.nodes.get(key="payer_acct")
    asset_node = contract.nodes.get(key="premium_asset")
    insurer_account = insurer_node.get_object()
    payer_account = payer_node.get_object()
    asset = asset_node.get_object()

    with db_transaction.atomic():
        index = contract.nodes.filter(node_type="obligation", key__startswith="claim_").count() + 1
        duty_key = f"claim_{index}"
        approval_key = f"claim_approval_{index}"

        ref = f"insurance-{contract.pk}-claim-{index}-{_uuid.uuid4().hex[:8]}"
        obligation = create_obligation(
            reference=ref,
            debtor_account=insurer_account,
            creditor_account=payer_account,
            asset=asset,
            amount=_from_base(amount_base_units, asset.decimals),
            due_at=due_at,
            order_reference=f"insurance:{contract.pk}",
        )

        condition = Condition.objects.create(
            name=f"{contract.name} — claim approval {index}",
            kind=ConditionKind.APPROVAL,
            status=ConditionStatus.PENDING,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            metadata={**meta, "archetype": "insurance", "claim_index": index},
        )

        event = ContractEvent.objects.create(
            kind=ContractEventKind.OBLIGATION_CREATED,
            title=f"Insurance claim {index}: {amount_base_units} {asset.unit_name}",
            obligation=obligation,
            condition=condition,
            source_type=_SOURCE_TYPE,
            source_id=str(contract.pk),
            payload={**meta, "archetype": "insurance", "claim_index": index},
        )

        create_node(contract, duty_key, "obligation",
                    title=f"Claim {index}: {amount_base_units} {asset.unit_name}",
                    object=obligation)
        create_node(contract, approval_key, "condition",
                    title=f"Claim approval {index}",
                    object=condition)

        create_edge(contract, "root", duty_key, "creates_duty",
                    label=f"claim {index}")
        create_edge(contract, approval_key, duty_key, "gates",
                    label="approval required")
        create_edge(contract, duty_key, "insurer_acct", "debtor")
        create_edge(contract, duty_key, "payer_acct", "creditor")
        create_edge(contract, duty_key, "premium_asset", "uses_asset")

    return {"obligation": obligation, "condition": condition, "event": event}


# ---------------------------------------------------------------------------
# approve_claim
# ---------------------------------------------------------------------------

def approve_claim(
    *,
    condition,
    approver=None,
    metadata: dict | None = None,
) -> Any:
    """
    Satisfy the approval condition for an insurance claim payout.

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
            title=f"Insurance claim approved: {condition.name}",
            condition=condition,
            actor=approver,
            source_type=condition.source_type,
            source_id=condition.source_id,
            payload={**meta, "archetype": "insurance"},
        )
    return event


# ---------------------------------------------------------------------------
# settle_claim
# ---------------------------------------------------------------------------

def settle_claim(
    *,
    obligation,
    contract: Contract | None = None,
    reference: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """
    Fulfill an insurance claim obligation via the assets backend.

    Delegates entirely to assets.services.assets.fulfill_obligation —
    holdings are never mutated directly here.

    Returns {"transaction": LedgerTransaction, "event": ContractEvent}.
    """
    from toto.assets.services.assets import fulfill_obligation
    from toto.claims.models import ContractEvent, ContractEventKind

    meta = dict(metadata or {})
    ref = reference or f"insurance-claim-settle-{obligation.reference}"
    source_id = str(contract.pk) if contract else obligation.order_reference

    with db_transaction.atomic():
        tx = fulfill_obligation(
            obligation=obligation,
            reference=ref,
            description=f"Insurance claim settlement: {obligation.reference}",
        )
        event = ContractEvent.objects.create(
            kind=ContractEventKind.SETTLEMENT,
            title=f"Insurance claim settled: {obligation.reference}",
            obligation=obligation,
            transaction=tx,
            source_type=_SOURCE_TYPE,
            source_id=source_id,
            payload={**meta, "archetype": "insurance", "reference": ref},
        )
    return {"transaction": tx, "event": event}
