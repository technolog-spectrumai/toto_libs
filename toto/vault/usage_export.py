"""
Vault usage exporter — builds usage statements from vault storage events.

Metric codes:
  storage.request     — API requests against this bucket
  storage.transfer_mb — bytes transferred (in MB)
  storage.mb_hour     — storage consumed (MB × hours)

This module imports metering helpers only.
It does NOT import tariffs, invoice, assets, or budget.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError
from django.utils import timezone as tz

from toto.metering.usage_statement import (
    build_usage_statement,
    dump_usage_statement_yaml,
    validate_usage_statement,
)

if TYPE_CHECKING:
    from toto.vault.models import VaultFile


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Internal: collect lines from vault storage events
# ---------------------------------------------------------------------------

def _collect_lines(
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
) -> list[dict]:
    """
    Query vault storage events for the given subject within the period.

    Currently builds synthetic summary lines; real implementations should
    query VaultFile / bucket-level metering records.
    """
    from django.db.models import Count, Sum
    from toto.vault.models import VaultFile

    lines: list[dict] = []

    if subject_type == "vault.Bucket":
        try:
            from toto.vault.models import Bucket
            bucket = Bucket.objects.get(pk=subject_id)
        except Exception:
            return lines

        files_qs = VaultFile.objects.filter(
            bucket=bucket,
            uploaded_at__gte=period_start,
            uploaded_at__lt=period_end,
        )

        request_count = files_qs.count()
        if request_count:
            lines.append({
                "line_id": f"storage-request-{subject_id}",
                "metric_code": "storage.request",
                "quantity": str(request_count),
                "unit": "request",
                "occurred_at": period_end.isoformat(),
                "source_type": "vault.Bucket",
                "source_id": str(subject_id),
                "source_label": str(bucket),
                "description": f"{request_count} file upload(s) in period",
            })

        total_bytes = files_qs.aggregate(s=Sum("file_size_bytes"))["s"] or 0
        total_mb = total_bytes / (1024 * 1024)
        if total_mb > 0:
            lines.append({
                "line_id": f"storage-transfer-{subject_id}",
                "metric_code": "storage.transfer_mb",
                "quantity": f"{total_mb:.6f}",
                "unit": "mb",
                "occurred_at": period_end.isoformat(),
                "source_type": "vault.Bucket",
                "source_id": str(subject_id),
                "source_label": str(bucket),
                "description": f"{total_mb:.2f} MB uploaded in period",
            })

    elif subject_type == "auth.User":
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(pk=subject_id)
        except Exception:
            return lines

        files_qs = VaultFile.objects.filter(
            owner=user,
            uploaded_at__gte=period_start,
            uploaded_at__lt=period_end,
        )
        request_count = files_qs.count()
        if request_count:
            lines.append({
                "line_id": f"storage-request-user-{subject_id}",
                "metric_code": "storage.request",
                "quantity": str(request_count),
                "unit": "request",
                "occurred_at": period_end.isoformat(),
                "source_type": "auth.User",
                "source_id": str(subject_id),
                "source_label": user.username,
                "description": f"{request_count} file upload(s) in period",
            })

    return lines


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def export_usage_statement(
    *,
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
    statement_id: str | None = None,
) -> dict:
    """Build and validate a usage statement dict for a vault subject."""
    subject_label = f"{subject_type}:{subject_id}"
    try:
        if subject_type == "vault.Bucket":
            from toto.vault.models import Bucket
            b = Bucket.objects.get(pk=subject_id)
            subject_label = str(b)
        elif subject_type == "auth.User":
            from django.contrib.auth import get_user_model
            u = get_user_model().objects.get(pk=subject_id)
            subject_label = u.username
    except Exception:
        pass

    lines = _collect_lines(subject_type, subject_id, period_start, period_end)
    if not lines:
        raise ValidationError({"lines": "No vault usage events found for this subject in the given period."})

    data = build_usage_statement(
        statement_id=statement_id or str(uuid.uuid4()),
        source_app="vault",
        source_label="Vault File Storage",
        exported_at=_utcnow_iso(),
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        subject_key=f"{subject_type}:{subject_id}",
        subject_type=subject_type,
        subject_id=str(subject_id),
        subject_label=subject_label,
        lines=lines,
    )
    return validate_usage_statement(data)


def export_usage_statement_yaml(
    *,
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
    statement_id: str | None = None,
) -> str:
    """Build a validated usage statement and return YAML text."""
    data = export_usage_statement(
        subject_type=subject_type,
        subject_id=subject_id,
        period_start=period_start,
        period_end=period_end,
        statement_id=statement_id,
    )
    return dump_usage_statement_yaml(data)


def export_usage_statement_to_vault(
    *,
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
    created_by=None,
    title: str | None = None,
) -> "VaultFile":
    """Build usage statement, save as VaultFile, return the VaultFile."""
    from django.core.files.base import ContentFile
    from toto.vault.models import VaultFile

    yaml_text = export_usage_statement_yaml(
        subject_type=subject_type,
        subject_id=subject_id,
        period_start=period_start,
        period_end=period_end,
    )
    safe_id = f"{subject_type.replace('.', '-')}-{subject_id}"
    filename = f"usage-statement-vault-{safe_id}-{period_start.date()}.yaml"
    if title is None:
        title = f"Vault Usage Statement {period_start.date()} / {period_end.date()} — {subject_type}:{subject_id}"

    owner = created_by
    if owner is None:
        from django.contrib.auth import get_user_model
        owner = get_user_model().objects.filter(is_superuser=True).first()
        if owner is None:
            raise ValidationError({"created_by": "No owner available; pass created_by."})

    vf = VaultFile(
        owner=owner,
        title=title,
        file_type="yaml",
    )
    vf.file.save(filename, ContentFile(yaml_text.encode()), save=False)
    vf.save()
    return vf
