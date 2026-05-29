"""Ledger entry importer — scans LedgerEntry for controlled budget accounts."""
from __future__ import annotations

from toto.treasury.importers.base import BudgetImportCandidate, BudgetImporter


class LedgerBudgetImporter(BudgetImporter):
    code = "ledger"
    label = "Ledger Transactions"
    description = "Import posted ledger entries for controlled budget accounts."
    icon = "fa-solid fa-book"

    def is_available(self) -> bool:
        from django.apps import apps
        return apps.is_installed("toto.assets")

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        from toto.assets.models import LedgerEntry
        from toto.treasury.models import BudgetLedgerAccount

        bindings = BudgetLedgerAccount.objects.filter(
            budget=budget, is_active=True
        ).select_related("ledger_account")
        account_map = {b.ledger_account_id: b for b in bindings}
        if not account_map:
            return []

        entries = LedgerEntry.objects.filter(
            account_id__in=account_map,
            asset=budget.asset,
        ).select_related("transaction", "account", "asset")

        candidates = []
        for entry in entries:
            amount = abs(entry.amount_base_units)
            if amount == 0:
                continue
            is_inflow = entry.amount_base_units > 0
            stream = "ledger_inflow" if is_inflow else "ledger_outflow"
            binding = account_map.get(entry.account_id)
            tx = entry.transaction
            booked_at = getattr(tx, "created_at", None) or entry.created_at
            desc = (
                f"Account: {entry.account.code}. "
                f"Transaction: {tx.reference}. "
                f"Type: {tx.transaction_type}. "
                f"Description: {tx.description}."
            )
            candidates.append(BudgetImportCandidate(
                import_key=f"assets.LedgerEntry:{entry.pk}",
                title=f"Ledger: {tx.reference[:60]}",
                description=desc,
                amount_base_units=amount,
                asset_id=entry.asset_id,
                stream_type_code=stream,
                source_type="assets.LedgerEntry",
                source_id=str(entry.pk),
                source_label=tx.reference,
                status="booked",
                ledger_transaction_id=entry.transaction_id,
                account_binding_id=binding.pk if binding else None,
                booked_at=booked_at,
                metadata={"transaction_type": tx.transaction_type},
            ))
        return candidates
