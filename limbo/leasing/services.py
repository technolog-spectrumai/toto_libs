from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Lease, LeaseStatus


def create_lease_contract(
    *,
    name: str,
    leased_object,
    lessor,
    lessee,
    billing_period: str,
    fixed_fee_base_units: int,
    starts_at,
    ends_at=None,
    lessor_account=None,
    lessee_account=None,
    payment_asset=None,
    metadata: dict | None = None,
) -> Lease:
    """
    Create a Lease backed by a contracts.Contract (archetype="lease").

    Returns the Lease instance (contract accessible via lease.contract).
    """
    from toto.contracts.models import Contract
    from toto.contracts.services import create_edge, create_node

    meta = dict(metadata or {})

    with transaction.atomic():
        contract = Contract.objects.create(
            name=name,
            description=(
                f"Lease of {leased_object} from {lessor} to {lessee}. "
                f"Billing: {billing_period}."
            ),
            status=Contract.STATUS_DRAFT,
            metadata={
                **meta,
                "archetype": "lease",
                "billing_period": billing_period,
                "fixed_fee_base_units": fixed_fee_base_units,
            },
        )

        lease = Lease.objects.create(
            contract=contract,
            leased_object=leased_object,
            lessor=lessor,
            lessee=lessee,
            lessor_account=lessor_account,
            lessee_account=lessee_account,
            payment_asset=payment_asset,
            billing_period=billing_period,
            fixed_fee_base_units=fixed_fee_base_units,
            starts_at=starts_at,
            ends_at=ends_at,
            metadata=meta,
        )

        create_node(contract, "root", "contract", title=name, object=contract)
        create_node(contract, "lessor_person", "manual",
                    title=str(lessor), object=lessor)
        create_node(contract, "lessee_person", "manual",
                    title=str(lessee), object=lessee)
        create_node(contract, "leased_object", "manual",
                    title=str(leased_object), object=leased_object)

        create_edge(contract, "root", "lessor_person", "creditor", label="lessor")
        create_edge(contract, "root", "lessee_person", "debtor", label="lessee")
        create_edge(contract, "root", "leased_object", "uses_asset", label="leased object")

        if lessor_account:
            create_node(contract, "lessor_acct", "ledger_account",
                        title=lessor_account.code, object=lessor_account)
            create_edge(contract, "root", "lessor_acct", "creditor", label="lessor account")

        if lessee_account:
            create_node(contract, "lessee_acct", "ledger_account",
                        title=lessee_account.code, object=lessee_account)
            create_edge(contract, "root", "lessee_acct", "debtor", label="lessee account")

        if payment_asset:
            create_node(contract, "payment_asset", "asset",
                        title=payment_asset.unit_name, object=payment_asset)
            create_edge(contract, "root", "payment_asset", "uses_asset",
                        label="payment currency")

    return lease


def activate_lease(lease: Lease) -> None:
    if lease.status != LeaseStatus.DRAFT:
        raise ValidationError("Only draft leases can be activated.")
    now = timezone.now()
    lease.status = LeaseStatus.ACTIVE
    lease.activated_at = now
    lease.next_billing_at = max(lease.starts_at, now)
    lease.save(update_fields=["status", "activated_at", "next_billing_at", "updated_at"])


def cancel_lease(lease: Lease) -> None:
    if lease.status not in (LeaseStatus.DRAFT, LeaseStatus.ACTIVE, LeaseStatus.PAUSED):
        raise ValidationError("Cannot cancel a lease in its current state.")
    lease.status = LeaseStatus.CANCELLED
    lease.cancelled_at = timezone.now()
    lease.save(update_fields=["status", "cancelled_at", "updated_at"])
