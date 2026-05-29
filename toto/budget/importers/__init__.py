"""
Budget importers — pull financial data from other apps into BudgetItems.

Importers are availability-guarded: they check is_installed() before importing
optional apps and fail gracefully when absent.
"""
from toto.budget.importers.base import BudgetImporterRegistry
from toto.budget.importers.obligations import ObligationBudgetImporter
from toto.budget.importers.ledger import LedgerBudgetImporter
from toto.budget.importers.invoices import InvoiceBudgetImporter
from toto.budget.importers.tariff_applications import TariffApplicationBudgetImporter
from toto.budget.importers.contracts import ContractBudgetImporter

registry = BudgetImporterRegistry()
registry.register(ObligationBudgetImporter())
registry.register(LedgerBudgetImporter())
registry.register(InvoiceBudgetImporter())
registry.register(TariffApplicationBudgetImporter())
registry.register(ContractBudgetImporter())

__all__ = ["registry"]
