"""
Budget stream type registry — defines default stream types seeded by
sync_treasury_stream_types management command.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class StreamTypeSpec:
    code: str
    name: str
    direction: str  # "inflow" or "outflow"
    namespace: str = ""
    description: str = ""
    is_system: bool = True
    sort_order: int = 100


class BudgetStreamRegistry:
    _specs: list[StreamTypeSpec] = []

    @classmethod
    def register(cls, spec: StreamTypeSpec) -> None:
        cls._specs.append(spec)

    @classmethod
    def all(cls) -> list[StreamTypeSpec]:
        return list(cls._specs)


def _reg(code, name, direction, namespace="", description="", sort_order=100):
    BudgetStreamRegistry.register(StreamTypeSpec(
        code=code, name=name, direction=direction,
        namespace=namespace, description=description,
        is_system=True, sort_order=sort_order,
    ))


# Generic
_reg("ledger_inflow", "Ledger Inflow", "inflow", namespace="generic", sort_order=10)
_reg("ledger_outflow", "Ledger Outflow", "outflow", namespace="generic", sort_order=11)
_reg("obligation_inflow", "Obligation Inflow", "inflow", namespace="generic", sort_order=12)
_reg("obligation_outflow", "Obligation Outflow", "outflow", namespace="generic", sort_order=13)
_reg("manual_inflow", "Manual Inflow", "inflow", namespace="generic", sort_order=14)
_reg("manual_outflow", "Manual Outflow", "outflow", namespace="generic", sort_order=15)
_reg("internal_transfer_in", "Internal Transfer In", "inflow", namespace="generic", sort_order=16)
_reg("internal_transfer_out", "Internal Transfer Out", "outflow", namespace="generic", sort_order=17)

# Invoices
_reg("invoice_receivable", "Invoice Receivable", "inflow", namespace="invoice", sort_order=20)
_reg("invoice_payment_received", "Invoice Payment Received", "inflow", namespace="invoice", sort_order=21)
_reg("vendor_invoice", "Vendor Invoice", "outflow", namespace="invoice", sort_order=22)
_reg("invoice_payment_made", "Invoice Payment Made", "outflow", namespace="invoice", sort_order=23)

# Tariff applications
_reg("tariff_usage_revenue", "Tariff Usage Revenue", "inflow", namespace="tariff", sort_order=30)
_reg("tariff_usage_cost", "Tariff Usage Cost", "outflow", namespace="tariff", sort_order=31)
_reg("usage_invoice_revenue", "Usage Invoice Revenue", "inflow", namespace="tariff", sort_order=32)
_reg("usage_invoice_cost", "Usage Invoice Cost", "outflow", namespace="tariff", sort_order=33)

# Loans
_reg("loan_disbursement", "Loan Disbursement", "inflow", namespace="loan", sort_order=40)
_reg("loan_repayment", "Loan Repayment", "outflow", namespace="loan", sort_order=41)
_reg("interest_income", "Interest Income", "inflow", namespace="loan", sort_order=42)
_reg("interest_expense", "Interest Expense", "outflow", namespace="loan", sort_order=43)

# Payroll
_reg("payroll", "Payroll", "outflow", namespace="payroll", sort_order=50)
_reg("contractor_payment", "Contractor Payment", "outflow", namespace="payroll", sort_order=51)
_reg("allowance", "Allowance", "outflow", namespace="payroll", sort_order=52)
_reg("bonus", "Bonus", "outflow", namespace="payroll", sort_order=53)

# Insurance
_reg("insurance_premium", "Insurance Premium", "outflow", namespace="insurance", sort_order=60)
_reg("insurance_payout", "Insurance Payout", "inflow", namespace="insurance", sort_order=61)
_reg("insurance_claim_payment", "Insurance Claim Payment", "outflow", namespace="insurance", sort_order=62)

# Operations
_reg("project_funding", "Project Funding", "inflow", namespace="operations", sort_order=70)
_reg("grant", "Grant", "inflow", namespace="operations", sort_order=71)
_reg("donation", "Donation", "inflow", namespace="operations", sort_order=72)
_reg("client_payment", "Client Payment", "inflow", namespace="operations", sort_order=73)
_reg("procurement", "Procurement", "outflow", namespace="operations", sort_order=74)
_reg("equipment_cost", "Equipment Cost", "outflow", namespace="operations", sort_order=75)
_reg("logistics_cost", "Logistics Cost", "outflow", namespace="operations", sort_order=76)
_reg("travel", "Travel", "outflow", namespace="operations", sort_order=77)
_reg("training", "Training", "outflow", namespace="operations", sort_order=78)
_reg("emergency_funding", "Emergency Funding", "inflow", namespace="operations", sort_order=79)
_reg("deployment_cost", "Deployment Cost", "outflow", namespace="operations", sort_order=80)
_reg("intervention_cost", "Intervention Cost", "outflow", namespace="operations", sort_order=81)
