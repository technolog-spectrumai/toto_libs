"""Budget importer base layer — BudgetImportCandidate + BudgetImporter protocol."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BudgetImportCandidate:
    import_key: str
    title: str
    description: str
    amount_base_units: int
    asset_id: int
    stream_type_code: str
    source_type: str = ""
    source_id: str = ""
    source_label: str = ""
    source_url: str = ""
    status: str = "planned"
    ledger_transaction_id: int | None = None
    obligation_id: int | None = None
    allocation_id: int | None = None
    contract_id: int | None = None
    account_binding_id: int | None = None
    counterparty_id: int | None = None
    due_at: Any = None
    booked_at: Any = None
    metadata: dict | None = None


class BudgetImporter:
    code: str = ""
    label: str = ""
    description: str = ""
    icon: str = "fa-solid fa-download"

    def is_available(self) -> bool:
        return True

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        raise NotImplementedError

    def candidate_count(self, budget) -> int:
        """Return number of candidates without full import (for preview UI)."""
        try:
            return len(self.list_candidates(budget))
        except Exception:
            return 0


class BudgetImporterRegistry:
    def __init__(self):
        self._importers: dict[str, BudgetImporter] = {}

    def register(self, importer: BudgetImporter) -> None:
        self._importers[importer.code] = importer

    def all(self) -> list[BudgetImporter]:
        return list(self._importers.values())

    def get(self, code: str) -> BudgetImporter | None:
        return self._importers.get(code)

    def available(self) -> list[BudgetImporter]:
        return [i for i in self._importers.values() if i.is_available()]
