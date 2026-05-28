"""
Insurance v1 — contract archetype.

Models an asset insurance policy as a contracts.Contract graph.
The insured party selects specific inventory assets; a premium is charged
on a recurring basis.

Public API
----------
create_insurance_contract(...)  → Contract
"""
from __future__ import annotations

from django.db import transaction as db_transaction
from django.utils import timezone

from .models import Contract
from .services import create_edge, create_node

_SOURCE_TYPE = "contracts.Contract"

PREMIUM_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("yearly", "Yearly"),
    ("biannual", "Bi-annual"),
    ("weekly", "Weekly"),
]


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
    metadata: dict | None = None,
) -> Contract:
    """
    Build an insurance contract archetype.

    Creates:
      - contracts.Contract (draft — requires signing before activation)
      - ContractNode/ContractEdge graph wiring all primitives together

    Parameters
    ----------
    name                       : unique contract name
    insured_person             : people.Person — the policy holder
    payer_account              : assets.LedgerAccount — pays premiums
    insurer_account            : assets.LedgerAccount — receives premiums
    premium_asset              : assets.Asset — currency of premium
    premium_amount_base_units  : premium amount in base units
    premium_frequency          : "monthly" / "quarterly" / "yearly" / …
    insured_items              : list of inventory.RealWorldObject to cover
    metadata                   : optional extra payload
    """
    meta = dict(metadata or {})

    with db_transaction.atomic():
        total_value = sum(
            int(item.estimated_value or 0) for item in insured_items
        )

        contract = Contract.objects.create(
            name=name,
            description=(
                f"Insurance policy for {insured_person} covering "
                f"{len(insured_items)} asset(s). "
                f"Premium: {premium_amount_base_units} {premium_asset.unit_name} / {premium_frequency}."
            ),
            status=Contract.STATUS_DRAFT,
            metadata={
                **meta,
                "archetype": "insurance",
                "premium_frequency": premium_frequency,
                "premium_amount_base_units": premium_amount_base_units,
                "insured_item_count": len(insured_items),
                "total_estimated_value": total_value,
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

        # One node per insured asset
        for i, item in enumerate(insured_items, start=1):
            create_node(contract, f"insured_item_{i}", "manual",
                        title=item.name, object=item)

        # Edges
        create_edge(contract, "root", "insured_person", "covers",
                    label="policy holder")
        create_edge(contract, "root", "payer_acct", "debtor",
                    label="premium payer")
        create_edge(contract, "root", "insurer_acct", "creditor",
                    label="insurer")
        create_edge(contract, "root", "premium_asset", "uses_asset",
                    label="premium currency")

        for i in range(1, len(insured_items) + 1):
            create_edge(contract, "root", f"insured_item_{i}", "insures",
                        label=f"covered asset {i}")

    return contract
