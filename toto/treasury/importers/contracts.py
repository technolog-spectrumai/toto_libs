"""
Contract archetype importer — enriches obligation-sourced items with contract context.
Does NOT import loan/payroll/insurance apps directly.
Reads contracts.Contract + claims.ContractEvent + contract metadata.
Prefers the obligations importer; this importer adds contract-level context.
"""
from __future__ import annotations

from toto.treasury.importers.base import BudgetImportCandidate, BudgetImporter

_ARCHETYPE_STREAM = {
    ("loan", "creditor"): "loan_disbursement",
    ("loan", "debtor"): "loan_repayment",
    ("payroll", "debtor"): "payroll",
    ("payroll", "creditor"): "obligation_inflow",
    ("insurance", "debtor"): "insurance_premium",
    ("insurance", "creditor"): "insurance_payout",
}


class ContractBudgetImporter(BudgetImporter):
    code = "contracts"
    label = "Contract Duties"
    description = "Import contract-linked duties (loan, payroll, insurance) with enriched context."
    icon = "fa-solid fa-file-signature"

    def is_available(self) -> bool:
        from django.apps import apps
        return apps.is_installed("toto.contracts") and apps.is_installed("toto.assets")

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        if not self.is_available():
            return []
        try:
            from toto.contracts.models import Contract
            from toto.assets.models import Obligation
            from toto.treasury.models import BudgetLedgerAccount
        except ImportError:
            return []

        bindings = BudgetLedgerAccount.objects.filter(
            budget=budget, is_active=True
        ).select_related("ledger_account")
        account_map = {b.ledger_account_id: b for b in bindings}
        if not account_map:
            return []

        archetypes = ["loan", "payroll", "insurance"]
        contracts = Contract.objects.filter(
            metadata__archetype__in=archetypes,
        )

        from django.db.models import Q
        obligations = Obligation.objects.filter(
            asset=budget.asset,
            order_reference__in=[f"{a}:{c.pk}" for a in archetypes for c in contracts],
        ).filter(
            Q(creditor_account_id__in=account_map) | Q(debtor_account_id__in=account_map)
        ).select_related("asset", "debtor_account", "creditor_account")

        # Build contract map by pk
        contract_map = {str(c.pk): c for c in contracts}

        candidates = []
        seen = set()

        for ob in obligations:
            order_ref = ob.order_reference or ""
            contract = None
            archetype = None
            for a in archetypes:
                prefix = f"{a}:"
                if order_ref.startswith(prefix):
                    archetype = a
                    contract_pk = order_ref[len(prefix):]
                    contract = contract_map.get(contract_pk)
                    break

            if not archetype or not contract:
                continue

            is_creditor = ob.creditor_account_id in account_map
            side = "creditor" if is_creditor else "debtor"
            stream = _ARCHETYPE_STREAM.get((archetype, side), "obligation_outflow")
            binding = account_map.get(ob.creditor_account_id if is_creditor else ob.debtor_account_id)

            key = f"contracts.Contract:{contract.pk}:{ob.pk}"
            if key in seen:
                continue
            seen.add(key)

            status_map = {
                "pending": "committed", "overdue": "committed",
                "fulfilled": "booked", "defaulted": "cancelled",
            }
            status = status_map.get(ob.status, "committed")
            booked_at = ob.fulfilled_at if ob.status == "fulfilled" else None

            desc = (
                f"Contract: {contract.name} (archetype: {archetype}). "
                f"Obligation: {ob.reference}. "
                f"Side: {side}. "
                f"Due: {ob.due_at}. Status: {ob.status}."
            )
            candidates.append(BudgetImportCandidate(
                import_key=f"contracts.Contract:{contract.pk}:ob:{ob.pk}",
                title=f"{archetype.capitalize()}: {contract.name}",
                description=desc,
                amount_base_units=ob.amount_base_units,
                asset_id=ob.asset_id,
                stream_type_code=stream,
                source_type="contracts.Contract",
                source_id=str(contract.pk),
                source_label=contract.name,
                status=status,
                obligation_id=ob.pk,
                contract_id=contract.pk,
                account_binding_id=binding.pk if binding else None,
                due_at=ob.due_at,
                booked_at=booked_at,
                metadata={"archetype": archetype, "order_reference": order_ref},
            ))
        return candidates
