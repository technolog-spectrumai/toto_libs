"""
Metering — Usage Statement helpers.

Defines the canonical YAML format for usage statements, validation, and
serialisation.  This module has NO imports from tariffs, assets, invoice,
budget, vault, ravioli, steven, or any other financial / source app.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# ---------------------------------------------------------------------------
# Forbidden keys — must never appear anywhere in a usage statement
# ---------------------------------------------------------------------------

_FORBIDDEN_KEYS = frozenset({
    "asset",
    "asset_id",
    "charged_asset",
    "amount",
    "amount_base_units",
    "price",
    "tariff",
    "tariff_item",
    "invoice",
    "invoice_id",
    "ledger_transaction",
    "ledger_account",
    "payer_account",
    "budget",
    "budget_item",
})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _check_forbidden_keys(data: Any, path: str = "") -> list[str]:
    """Return list of paths where forbidden keys appear."""
    errors: list[str] = []
    if isinstance(data, dict):
        for key, value in data.items():
            current = f"{path}.{key}" if path else key
            if key in _FORBIDDEN_KEYS:
                errors.append(f"Forbidden key '{key}' found at {current!r}")
            errors.extend(_check_forbidden_keys(value, current))
    elif isinstance(data, list):
        for i, item in enumerate(data):
            errors.extend(_check_forbidden_keys(item, f"{path}[{i}]"))
    return errors


def _parse_iso(value: Any, field: str) -> datetime:
    if not isinstance(value, (str, datetime)):
        raise ValidationError({field: _(f"Must be an ISO datetime string, got {type(value).__name__}.")})
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError({field: _(f"'{value}' is not a valid ISO datetime.")})


def _parse_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, (int, float)):
        raise ValidationError({field: _("Quantity must be a string representation of Decimal, not a number literal.")})
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValidationError({field: _(f"'{value}' is not a valid Decimal string.")})
    if d <= 0:
        raise ValidationError({field: _("Quantity must be positive (> 0).")})
    return d


# ---------------------------------------------------------------------------
# 1. build_usage_statement
# ---------------------------------------------------------------------------

def build_usage_statement(
    *,
    statement_id: str | None = None,
    source_app: str,
    source_label: str = "",
    exported_at: str | datetime,
    period_start: str | datetime,
    period_end: str | datetime,
    subject_key: str,
    subject_type: str,
    subject_id: str,
    subject_label: str = "",
    lines: list[dict],
) -> dict:
    """Build a usage statement dict ready for validation and serialisation."""
    if statement_id is None:
        statement_id = str(uuid.uuid4())

    def _iso(v):
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)

    return {
        "kind": "usage_statement",
        "version": 1,
        "statement_id": statement_id,
        "source_app": source_app,
        "source_label": source_label,
        "exported_at": _iso(exported_at),
        "period": {
            "start": _iso(period_start),
            "end": _iso(period_end),
        },
        "subject": {
            "key": subject_key,
            "type": subject_type,
            "id": str(subject_id),
            "label": subject_label,
        },
        "lines": lines,
    }


# ---------------------------------------------------------------------------
# 2. validate_usage_statement
# ---------------------------------------------------------------------------

def validate_usage_statement(data: dict) -> dict:
    """
    Validate a usage statement dict.

    Returns a normalised copy (quantities as Decimal, datetimes as str ISO).
    Raises django.core.exceptions.ValidationError on any failure.
    """
    errors: dict[str, Any] = {}

    if not isinstance(data, dict):
        raise ValidationError({"__all__": _("Usage statement must be a dict.")})

    # Forbidden keys
    forbidden_errors = _check_forbidden_keys(data)
    if forbidden_errors:
        raise ValidationError({"__all__": forbidden_errors})

    # kind
    if data.get("kind") != "usage_statement":
        errors["kind"] = _("Must be 'usage_statement'.")

    # version
    if data.get("version") != 1:
        errors["version"] = _("Must be 1.")

    # statement_id
    if not data.get("statement_id"):
        errors["statement_id"] = _("Required.")

    # source_app
    if not data.get("source_app"):
        errors["source_app"] = _("Required.")

    # exported_at
    exported_at_str = data.get("exported_at")
    if not exported_at_str:
        errors["exported_at"] = _("Required.")
    else:
        try:
            _parse_iso(exported_at_str, "exported_at")
        except ValidationError as exc:
            errors.update(exc.message_dict)

    # period
    period = data.get("period")
    if not isinstance(period, dict):
        errors["period"] = _("Must be a dict with 'start' and 'end'.")
    else:
        try:
            p_start = _parse_iso(period.get("start", ""), "period.start")
        except ValidationError as exc:
            errors.update(exc.message_dict)
            p_start = None
        try:
            p_end = _parse_iso(period.get("end", ""), "period.end")
        except ValidationError as exc:
            errors.update(exc.message_dict)
            p_end = None
        if p_start and p_end and p_end <= p_start:
            errors["period.end"] = _("period.end must be after period.start.")

    # subject
    subject = data.get("subject")
    if not isinstance(subject, dict):
        errors["subject"] = _("Must be a dict with 'key', 'type', 'id'.")
    else:
        if not subject.get("key"):
            errors["subject.key"] = _("Required.")
        if not subject.get("type"):
            errors["subject.type"] = _("Required.")
        if not str(subject.get("id", "")).strip():
            errors["subject.id"] = _("Required.")

    # lines
    lines = data.get("lines")
    if not isinstance(lines, list) or len(lines) == 0:
        errors["lines"] = _("Must be a non-empty list.")
    else:
        seen_ids: set[str] = set()
        for i, line in enumerate(lines):
            prefix = f"lines[{i}]"
            if not isinstance(line, dict):
                errors[prefix] = _("Each line must be a dict.")
                continue

            # line_id
            line_id = line.get("line_id")
            if not line_id:
                errors[f"{prefix}.line_id"] = _("Required.")
            elif line_id in seen_ids:
                errors[f"{prefix}.line_id"] = _(f"Duplicate line_id '{line_id}'.")
            else:
                seen_ids.add(line_id)

            # metric_code
            if not line.get("metric_code"):
                errors[f"{prefix}.metric_code"] = _("Required.")

            # quantity
            qty_raw = line.get("quantity")
            if qty_raw is None:
                errors[f"{prefix}.quantity"] = _("Required.")
            else:
                try:
                    _parse_decimal(qty_raw, f"{prefix}.quantity")
                except ValidationError as exc:
                    errors.update(exc.message_dict)

            # unit
            if "unit" not in line:
                errors[f"{prefix}.unit"] = _("Required.")

            # occurred_at
            occ = line.get("occurred_at")
            if not occ:
                errors[f"{prefix}.occurred_at"] = _("Required.")
            else:
                try:
                    _parse_iso(occ, f"{prefix}.occurred_at")
                except ValidationError as exc:
                    errors.update(exc.message_dict)

    if errors:
        raise ValidationError(errors)

    return data


# ---------------------------------------------------------------------------
# 3. dump_usage_statement_yaml
# ---------------------------------------------------------------------------

class _DecimalSafeStr(str):
    """Marker so we can yaml-dump Decimals as bare strings."""


def _to_yaml_safe(obj: Any) -> Any:
    """Recursively convert Decimal → str for YAML serialisation."""
    if isinstance(obj, dict):
        return {k: _to_yaml_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_yaml_safe(i) for i in obj]
    if isinstance(obj, Decimal):
        return str(obj)
    return obj


def dump_usage_statement_yaml(data: dict) -> str:
    """Validate then serialise to YAML string. Decimal values are strings."""
    validate_usage_statement(data)
    safe = _to_yaml_safe(data)
    return yaml.dump(safe, default_flow_style=False, allow_unicode=True, sort_keys=True)


# ---------------------------------------------------------------------------
# 4. load_usage_statement_yaml
# ---------------------------------------------------------------------------

def load_usage_statement_yaml(text: str) -> dict:
    """Parse YAML safely, validate, return normalised dict."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError({"__all__": _(f"YAML parse error: {exc}")})
    if not isinstance(data, dict):
        raise ValidationError({"__all__": _("YAML root must be a mapping.")})
    return validate_usage_statement(data)
