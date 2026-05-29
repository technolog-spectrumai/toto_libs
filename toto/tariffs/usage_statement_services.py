"""
Tariff — Usage Statement services.

Flow:
  1. read_usage_statement_from_vault_file — read + validate YAML from VaultFile
  2. preview_usage_statement              — parse YAML, return human-readable summary (no DB)
  3. price_usage_statement                — parse + price all lines (no DB writes)
  4. apply_tariff_to_usage_statement      — atomic: TariffApplication + Invoice

Rules:
  - Does NOT post ledger transactions
  - Does NOT create assets.Obligation
  - Does NOT mutate AssetHolding
  - Does NOT create PaymentSettlement
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from toto.metering.usage_statement import load_usage_statement_yaml
from toto.assets.models import from_base_units

from .models import (
    TariffApplication,
    TariffApplicationLine,
    TariffApplicationStatus,
    Tariff,
    TariffItem,
)
from .services import _calculate_charge


# ---------------------------------------------------------------------------
# 1. read_usage_statement_from_vault_file
# ---------------------------------------------------------------------------

def read_usage_statement_from_vault_file(usage_file) -> dict:
    """Read YAML text from VaultFile, validate, return normalised dict."""
    try:
        usage_file.file.seek(0)
        text = usage_file.file.read()
    except Exception as exc:
        raise ValidationError({"usage_file": _(f"Cannot read file: {exc}")})

    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError({"usage_file": _(f"File is not valid UTF-8: {exc}")})

    return load_usage_statement_yaml(text)


# ---------------------------------------------------------------------------
# 2. preview_usage_statement
# ---------------------------------------------------------------------------

def preview_usage_statement(*, usage_file) -> dict:
    """
    Read and validate YAML; return a structured preview dict.
    No pricing, no DB writes.
    """
    data = read_usage_statement_from_vault_file(usage_file)

    period = data["period"]
    subject = data["subject"]
    lines = data["lines"]

    # Metrics summary
    summary: dict[str, dict] = {}
    for line in lines:
        code = line["metric_code"]
        if code not in summary:
            summary[code] = {"metric_code": code, "total_quantity": Decimal("0"), "unit": line.get("unit", ""), "line_count": 0}
        summary[code]["total_quantity"] += Decimal(str(line["quantity"]))
        summary[code]["line_count"] += 1

    return {
        "statement_id": data["statement_id"],
        "source_app": data.get("source_app", ""),
        "source_label": data.get("source_label", ""),
        "period_start": period["start"],
        "period_end": period["end"],
        "subject": subject,
        "line_count": len(lines),
        "metrics_summary": list(summary.values()),
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# Internal: price a single YAML line against a Tariff
# ---------------------------------------------------------------------------

def _price_line(line: dict, tariff: Tariff) -> dict:
    """
    Return a priced line dict, or an error dict if no TariffItem matches.
    No DB writes.
    """
    metric_code = line["metric_code"]
    quantity = Decimal(str(line["quantity"]))
    unit = line.get("unit", "")

    items = tariff.active_items.filter(metric__code=metric_code).select_related(
        "metric", "charged_asset", "receiving_account", "unit"
    )
    if unit:
        from django.db.models import Q
        items = items.filter(Q(unit__isnull=True) | Q(unit__code=unit))

    items = list(items)
    if not items:
        return {
            "line_id": line["line_id"],
            "metric_code": metric_code,
            "quantity": quantity,
            "unit": unit,
            "error": f"No active TariffItem for metric '{metric_code}' in tariff '{tariff.code}'.",
        }

    charges = []
    for item in items:
        amount = _calculate_charge(item, quantity)
        asset = item.charged_asset
        charges.append({
            "tariff_item_id": item.pk,
            "tariff_item_name": item.name,
            "charged_asset": asset.unit_name,
            "charged_asset_id": asset.pk,
            "charged_asset_decimals": asset.decimals,
            "amount_base_units": amount,
            "amount_display": from_base_units(amount, asset.decimals),
        })

    return {
        "line_id": line["line_id"],
        "metric_code": metric_code,
        "quantity": quantity,
        "unit": unit,
        "occurred_at": line.get("occurred_at"),
        "source_label": line.get("source_label", ""),
        "description": line.get("description", ""),
        "charges": charges,
    }


# ---------------------------------------------------------------------------
# 3. price_usage_statement
# ---------------------------------------------------------------------------

def price_usage_statement(*, usage_file, tariff: Tariff) -> dict:
    """
    Read + validate YAML; price all lines.
    Returns a pricing preview dict — no DB writes.
    """
    data = read_usage_statement_from_vault_file(usage_file)
    lines = data["lines"]

    priced_lines = []
    errors = []
    totals_by_asset: dict[str, dict] = {}

    for line in lines:
        result = _price_line(line, tariff)
        if "error" in result:
            errors.append(result)
        else:
            priced_lines.append(result)
            for charge in result["charges"]:
                asset_name = charge["charged_asset"]
                if asset_name not in totals_by_asset:
                    totals_by_asset[asset_name] = {
                        "asset": asset_name,
                        "base_units": 0,
                        "display": Decimal("0"),
                    }
                totals_by_asset[asset_name]["base_units"] += charge["amount_base_units"]
                totals_by_asset[asset_name]["display"] += charge["amount_display"]

    return {
        "statement_id": data["statement_id"],
        "source_app": data.get("source_app", ""),
        "tariff_code": tariff.code,
        "tariff_name": tariff.name,
        "totals_by_asset": list(totals_by_asset.values()),
        "priced_lines": priced_lines,
        "errors": errors,
        "has_errors": bool(errors),
    }


# ---------------------------------------------------------------------------
# 4. apply_tariff_to_usage_statement
# ---------------------------------------------------------------------------

def apply_tariff_to_usage_statement(
    *,
    usage_file,
    tariff: Tariff,
    issued_to,
    issued_by=None,
    due_date=None,
    title: str | None = None,
    description: str = "",
    override_source_app: str = "",
    override_subject_label: str = "",
) -> tuple:
    """
    Parse YAML, price all lines, then atomically create:
      - TariffApplication
      - TariffApplicationLine (one per YAML line)
      - Invoice
      - InvoiceLine (one per TariffApplicationLine)

    Returns (application, invoice).

    Does NOT:
      - Create assets.Obligation
      - Post LedgerTransaction
      - Mutate AssetHolding
      - Create PaymentSettlement
    """
    from toto.invoice.models import Invoice, InvoiceLine, InvoiceStatus
    from toto.assets.models import Asset

    data = read_usage_statement_from_vault_file(usage_file)
    statement_id = data["statement_id"]

    # Reject duplicate (unless cancelled/failed)
    existing = TariffApplication.objects.filter(statement_id=statement_id).first()
    if existing and existing.status not in (
        TariffApplicationStatus.CANCELLED,
        TariffApplicationStatus.FAILED,
    ):
        raise ValidationError({
            "statement_id": _(
                f"A TariffApplication for statement '{statement_id}' already exists "
                f"(status: {existing.get_status_display()})."
            )
        })

    # Price all lines
    lines = data["lines"]
    priced_lines = []
    errors = []

    for line in lines:
        result = _price_line(line, tariff)
        if "error" in result:
            errors.append(result)
        else:
            priced_lines.append((line, result))

    if errors:
        msgs = [e["error"] for e in errors]
        raise ValidationError({"lines": msgs})

    if not priced_lines:
        raise ValidationError({"lines": _("No lines to invoice.")})

    # Determine invoice currency from first charge's asset
    first_charge = priced_lines[0][1]["charges"][0]
    currency_label = first_charge["charged_asset"]

    # Invoice title
    period = data["period"]
    subject = data["subject"]
    if not title:
        title = (
            f"Usage Invoice — {data.get('source_app', '')} — "
            f"{subject.get('key', '')} — "
            f"{period['start'][:10]} / {period['end'][:10]}"
        )

    # Total invoice amount (sum of display amounts across all charges)
    total_amount = Decimal("0")
    for _line, priced in priced_lines:
        for charge in priced["charges"]:
            total_amount += charge["amount_display"]

    with transaction.atomic():
        # Create TariffApplication
        app = TariffApplication.objects.create(
            statement_id=statement_id,
            usage_file=usage_file,
            tariff=tariff,
            detected_source_app=data.get("source_app", ""),
            detected_subject_key=subject.get("key", ""),
            detected_subject_type=subject.get("type", ""),
            detected_subject_id=subject.get("id", ""),
            detected_subject_label=subject.get("label", ""),
            override_source_app=override_source_app,
            override_subject_label=override_subject_label,
            period_start=_parse_dt(period["start"]),
            period_end=_parse_dt(period["end"]),
            status=TariffApplicationStatus.INVOICED,
            title=title,
            description=description,
            created_by=issued_by,
        )

        # Create Invoice
        invoice = Invoice.objects.create(
            issued_to=issued_to,
            issued_by=issued_by,
            title=title,
            description=description,
            amount=total_amount,
            currency_label=currency_label,
            status=InvoiceStatus.PENDING,
            due_date=due_date,
        )

        # Create lines
        for raw_line, priced in priced_lines:
            for charge in priced["charges"]:
                asset = Asset.objects.get(pk=charge["charged_asset_id"])
                app_line = TariffApplicationLine.objects.create(
                    application=app,
                    line_id=raw_line["line_id"],
                    metric_code=raw_line["metric_code"],
                    quantity=Decimal(str(raw_line["quantity"])),
                    unit=raw_line.get("unit", ""),
                    occurred_at=_parse_dt_nullable(raw_line.get("occurred_at")),
                    source_type=raw_line.get("source_type", ""),
                    source_id=raw_line.get("source_id", ""),
                    source_label=raw_line.get("source_label", ""),
                    description=raw_line.get("description", ""),
                    tariff_item_id=charge["tariff_item_id"],
                    charged_asset=asset,
                    amount_base_units=charge["amount_base_units"],
                    amount_display=charge["amount_display"],
                )

                inv_line = InvoiceLine.objects.create(
                    invoice=invoice,
                    title=f"{raw_line['metric_code']} × {raw_line['quantity']} {raw_line.get('unit', '')}".strip(),
                    description=raw_line.get("description", ""),
                    quantity=Decimal(str(raw_line["quantity"])),
                    unit=raw_line.get("unit", ""),
                    asset=asset,
                    amount_base_units=charge["amount_base_units"],
                    amount=charge["amount_display"],
                    source_type="tariffs.TariffApplicationLine",
                    source_id=str(app_line.pk),
                    source_label=raw_line.get("source_label", ""),
                )
                app_line.invoice_line_source_type = "invoice.InvoiceLine"
                app_line.invoice_line_source_id = str(inv_line.pk)
                app_line.save(update_fields=["invoice_line_source_type", "invoice_line_source_id"])

        # Link application → invoice
        app.invoice_source_type = "invoice.Invoice"
        app.invoice_source_id = str(invoice.pk)
        app.status = TariffApplicationStatus.INVOICED
        app.save(update_fields=["invoice_source_type", "invoice_source_id", "status", "updated_at"])

    return app, invoice


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_dt(value) -> datetime:
    if isinstance(value, datetime):
        return value
    dt = datetime.fromisoformat(str(value))
    if dt.tzinfo is None:
        from django.utils.timezone import make_aware
        dt = make_aware(dt)
    return dt


def _parse_dt_nullable(value) -> datetime | None:
    if not value:
        return None
    try:
        return _parse_dt(value)
    except Exception:
        return None
