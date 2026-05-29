"""
Tariff application importer — pulls TariffApplicationLine into BudgetItems.
Does NOT read raw usage YAML. Reads priced/audited TariffApplicationLine records.
"""
from __future__ import annotations

from toto.treasury.importers.base import BudgetImportCandidate, BudgetImporter


def _app_status(app_status: str, invoice_paid: bool) -> str:
    if invoice_paid:
        return "booked"
    if app_status == "invoiced":
        return "committed"
    if app_status in ("failed", "cancelled"):
        return "cancelled"
    return "planned"


class TariffApplicationBudgetImporter(BudgetImporter):
    code = "tariff_applications"
    label = "Tariff Applications"
    description = "Import priced tariff application lines (usage charges) into budget."
    icon = "fa-solid fa-receipt"

    def is_available(self) -> bool:
        from django.apps import apps
        if not apps.is_installed("toto.tariffs"):
            return False
        try:
            from toto.tariffs.models import TariffApplicationLine  # noqa
            return True
        except ImportError:
            return False

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        if not self.is_available():
            return []
        try:
            from toto.tariffs.models import TariffApplication, TariffApplicationLine
        except ImportError:
            return []

        lines = TariffApplicationLine.objects.filter(
            charged_asset=budget.asset,
        ).select_related(
            "application", "application__tariff", "charged_asset", "tariff_item__metric"
        )

        candidates = []
        for line in lines:
            app = line.application

            # Check if linked invoice is paid
            invoice_paid = False
            if app.invoice_source_type == "invoice.Invoice" and app.invoice_source_id:
                try:
                    from toto.invoice.models import Invoice
                    inv = Invoice.objects.filter(pk=int(app.invoice_source_id)).first()
                    invoice_paid = inv and inv.status == "paid"
                except Exception:
                    pass

            status = _app_status(app.status, invoice_paid)

            # Determine stream type — default cost (outflow) for usage charges
            if app.invoice_source_id:
                stream = "usage_invoice_cost"
            else:
                stream = "tariff_usage_cost"

            title = f"Usage charge: {line.metric_code}"
            desc = (
                f"Statement: {app.statement_id}. "
                f"Source app: {app.effective_source_app}. "
                f"Subject: {app.detected_subject_key}. "
                f"Metric: {line.metric_code}. "
                f"Quantity: {line.quantity} {line.unit}. "
                f"Tariff: {app.tariff.name}."
            )
            if app.invoice_source_id:
                desc += f" Invoice: {app.invoice_source_type}:{app.invoice_source_id}."

            candidates.append(BudgetImportCandidate(
                import_key=f"tariffs.TariffApplicationLine:{line.pk}",
                title=title,
                description=desc,
                amount_base_units=line.amount_base_units,
                asset_id=line.charged_asset_id,
                stream_type_code=stream,
                source_type="tariffs.TariffApplicationLine",
                source_id=str(line.pk),
                source_label=f"{app.effective_source_app}:{app.detected_subject_key}",
                status=status,
                metadata={
                    "statement_id": app.statement_id,
                    "metric_code": line.metric_code,
                    "quantity": str(line.quantity),
                    "unit": line.unit,
                    "tariff_code": app.tariff.code,
                },
            ))
        return candidates
