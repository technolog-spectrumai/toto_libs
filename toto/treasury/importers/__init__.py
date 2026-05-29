"""
Budget importers — pull financial data from other apps into BudgetItems.

Importers are availability-guarded: they check is_installed() before importing
optional apps and fail gracefully when absent.
"""
from toto.treasury.importers.base import BudgetImporterRegistry
from toto.treasury.importers.obligations import ObligationBudgetImporter
from toto.treasury.importers.ledger import LedgerBudgetImporter
from toto.treasury.importers.invoices import InvoiceBudgetImporter
from toto.treasury.importers.tariff_applications import TariffApplicationBudgetImporter
from toto.treasury.importers.contracts import ContractBudgetImporter

registry = BudgetImporterRegistry()
registry.register(ObligationBudgetImporter())
registry.register(LedgerBudgetImporter())
registry.register(InvoiceBudgetImporter())
registry.register(TariffApplicationBudgetImporter())
registry.register(ContractBudgetImporter())

__all__ = ["registry"]
