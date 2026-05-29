"""Invoice importer — pulls Invoice and InvoiceLine data into BudgetItems."""
from __future__ import annotations

from toto.treasury.importers.base import BudgetImportCandidate, BudgetImporter

_INVOICE_STATUS_MAP = {
    "pending": "planned",
    "overdue": "committed",
    "paid": "booked",
    "cancelled": "cancelled",
}

_USAGE_SOURCE_TYPES = {"tariffs.TariffApplicationLine", "tariffs.TariffApplication"}


def _is_usage_invoice(invoice) -> bool:
    try:
        for line in invoice.lines.all():
            if line.source_type in _USAGE_SOURCE_TYPES:
                return True
    except Exception:
        pass
    return False


def _invoice_stream(invoice, is_inflow: bool) -> str:
    if _is_usage_invoice(invoice):
        return "usage_invoice_revenue" if is_inflow else "usage_invoice_cost"
    if invoice.status == "paid":
        return "invoice_payment_received" if is_inflow else "invoice_payment_made"
    return "invoice_receivable" if is_inflow else "vendor_invoice"


class InvoiceBudgetImporter(BudgetImporter):
    code = "invoices"
    label = "Invoices"
    description = "Import invoices and invoice lines matching the budget asset."
    icon = "fa-solid fa-file-invoice-dollar"

    def is_available(self) -> bool:
        from django.apps import apps
        return apps.is_installed("toto.invoice")

    def list_candidates(self, budget) -> list[BudgetImportCandidate]:
        if not self.is_available():
            return []
        try:
            from toto.invoice.models import Invoice
        except ImportError:
            return []

        from django.db.models import Q

        # Match by asset (via InvoiceLine.asset) or currency_label
        try:
            from toto.invoice.models import InvoiceLine
            has_invoice_line = True
        except ImportError:
            has_invoice_line = False

        invoices = Invoice.objects.exclude(status="cancelled").select_related("issued_to", "issued_by")
        if has_invoice_line:
            invoices = invoices.prefetch_related("lines__asset")

        candidates = []
        for invoice in invoices:
            # Determine if this invoice matches the budget asset
            if has_invoice_line:
                matched_lines = [
                    l for l in invoice.lines.all()
                    if l.asset_id == budget.asset_id or (not l.asset_id and invoice.currency_label == budget.asset.unit_name)
                ]
            else:
                matched_lines = []

            # Determine direction (assume receivable/inflow by default)
            is_inflow = True
            stream = _invoice_stream(invoice, is_inflow)
            item_status = _INVOICE_STATUS_MAP.get(invoice.status, "planned")
            booked_at = invoice.paid_at if invoice.status == "paid" else None

            if matched_lines:
                for line in matched_lines:
                    if line.amount_base_units:
                        amount = line.amount_base_units
                    else:
                        from decimal import Decimal
                        amount = int(line.amount * (10 ** budget.asset.decimals))

                    title = f"{invoice.title}: {line.title}"
                    desc = (
                        f"Invoice #{invoice.pk}: {invoice.title}. "
                        f"Line: {line.description}. "
                        f"Due: {invoice.due_date}. Status: {invoice.get_status_display()}."
                    )
                    candidates.append(BudgetImportCandidate(
                        import_key=f"invoice.InvoiceLine:{line.pk}",
                        title=title[:255],
                        description=desc,
                        amount_base_units=amount,
                        asset_id=line.asset_id or budget.asset_id,
                        stream_type_code=stream,
                        source_type="invoice.InvoiceLine",
                        source_id=str(line.pk),
                        source_label=line.title or invoice.title,
                        status=item_status,
                        due_at=None,
                        booked_at=booked_at,
                        metadata={"invoice_id": invoice.pk, "invoice_status": invoice.status},
                    ))
            elif invoice.currency_label == budget.asset.unit_name:
                # Fallback: import at invoice level
                from decimal import Decimal
                amount = int(invoice.amount * (10 ** budget.asset.decimals))
                desc = (
                    f"Invoice #{invoice.pk}: {invoice.title}. "
                    f"{invoice.description}. "
                    f"Due: {invoice.due_date}. Status: {invoice.get_status_display()}."
                )
                candidates.append(BudgetImportCandidate(
                    import_key=f"invoice.Invoice:{invoice.pk}",
                    title=invoice.title[:255],
                    description=desc,
                    amount_base_units=amount,
                    asset_id=budget.asset_id,
                    stream_type_code=stream,
                    source_type="invoice.Invoice",
                    source_id=str(invoice.pk),
                    source_label=invoice.title,
                    status=item_status,
                    due_at=None,
                    booked_at=booked_at,
                    metadata={"invoice_status": invoice.status},
                ))
        return candidates
