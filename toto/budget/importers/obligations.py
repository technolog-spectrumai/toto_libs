"""
Obligation importer — scans assets.Obligation involving controlled budget accounts.
"""
from __future__ import annotations

from toto.budget.importers.base import BudgetImportCandidate, BudgetImporter

# Order-reference prefix → stream type mappings
_ORDER_STREAM_MAP = {
    "loan:": {
        "creditor": "loan_disbursement",
        "debtor": "loan_repayment",
    },
    "payroll:": {
        "debtor": "payroll",
        "creditor": "obligation_inflow",
    },
    "invoice:": {
        "creditor": "invoice_payment_received",
        "debtor": "invoice_payment_made",
    },
    "insurance:": {
        "debtor": "insurance_premium",
        "creditor": "insurance_payout",
    },
}

_STATUS_MAP = {
    "pending": "committed",
    "overdue": "committed",
    "fulfilled": "booked",
    "defaulted": "cancelled",
}


def _resolve_stream_type(order_reference: str, side: str, default: str) -> str:
    for prefix, mapping in _ORDER_STREAM_MAP.items():
        if order_reference.startswith(prefix):
            return mapping.get(side, default)
    return default


class ObligationBudgetImporter(BudgetImporter):
    code = "obligations"
    label = "Obligations"
    description = "Import asset obligations involving controlled budget accounts."
    icon = "fa-solid fa-file-contract"

    def is_available(self) -> bool:
        from django.apps import apps
        return apps.is_installed("toto.assets")

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        from toto.assets.models import Obligation
        from toto.budget.models import BudgetLedgerAccount

        bindings = BudgetLedgerAccount.objects.filter(
            budget=budget, is_active=True
        ).select_related("ledger_account")

        account_map = {b.ledger_account_id: b for b in bindings}
        if not account_map:
            return []

        from django.db.models import Q
        obligations = Obligation.objects.filter(
            asset=budget.asset,
        ).filter(
            Q(creditor_account_id__in=account_map) | Q(debtor_account_id__in=account_map)
        ).select_related("asset", "debtor_account", "creditor_account")

        candidates = []
        for ob in obligations:
            is_creditor = ob.creditor_account_id in account_map
            is_debtor = ob.debtor_account_id in account_map
            side = "creditor" if is_creditor else "debtor"
            default_stream = "obligation_inflow" if is_creditor else "obligation_outflow"
            stream = _resolve_stream_type(ob.order_reference or "", side, default_stream)
            binding = account_map.get(ob.creditor_account_id if is_creditor else ob.debtor_account_id)
            status = _STATUS_MAP.get(ob.status, "committed")
            booked_at = ob.fulfilled_at if ob.status == "fulfilled" else None
            desc = (
                f"Debtor: {ob.debtor_account.code} → Creditor: {ob.creditor_account.code}. "
                f"Reference: {ob.reference}. "
                f"Order ref: {ob.order_reference or '—'}. "
                f"Due: {ob.due_at}. Status: {ob.status}."
            )
            candidates.append(BudgetImportCandidate(
                import_key=f"assets.Obligation:{ob.pk}",
                title=f"Obligation {ob.reference}",
                description=desc,
                amount_base_units=ob.amount_base_units,
                asset_id=ob.asset_id,
                stream_type_code=stream,
                source_type="assets.Obligation",
                source_id=str(ob.pk),
                source_label=ob.reference,
                status=status,
                obligation_id=ob.pk,
                account_binding_id=binding.pk if binding else None,
                due_at=ob.due_at,
                booked_at=booked_at,
                metadata={"order_reference": ob.order_reference or ""},
            ))
        return candidates
