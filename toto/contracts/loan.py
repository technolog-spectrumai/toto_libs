"""
Loan v1 — contract archetype.

Models a same-currency loan as a contracts.Contract graph.
Lender disburses principal in one asset; borrower repays in the same asset.

Public API
----------
create_loan_contract(...)  → Contract
"""
from __future__ import annotations

from django.db import transaction as db_transaction

from .models import Contract
from .services import create_edge, create_node

_SOURCE_TYPE = "contracts.Contract"

REPAYMENT_FREQUENCIES = [
    ("monthly", "Monthly"),
    ("quarterly", "Quarterly"),
    ("biannual", "Bi-annual"),
    ("yearly", "Yearly"),
    ("bullet", "Bullet (at maturity)"),
]


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

    with db_transaction.atomic():
        interest_pct = interest_rate_bps / 100

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
