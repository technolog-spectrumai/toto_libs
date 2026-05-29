"""
Ravioli usage exporter — builds usage statements from graph events.

Metric codes:
  ravioli.cypher_query  — CypherQuery executions
  ravioli.cypher_row    — rows returned by queries
  ravioli.graph_event   — GraphChangeEvents processed
  ravioli.graph_write   — write operations to the graph

This module imports metering helpers only.
It does NOT import tariffs, invoice, assets, or budget.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from django.core.exceptions import ValidationError

from toto.metering.usage_statement import (
    build_usage_statement,
    dump_usage_statement_yaml,
    validate_usage_statement,
)

if TYPE_CHECKING:
    from toto.vault.models import VaultFile


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _collect_lines(
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
) -> list[dict]:
    lines: list[dict] = []

    if subject_type == "auth.User":
        try:
            from django.contrib.auth import get_user_model
            from toto.ravioli.models import CypherQueryResult, GraphChangeEvent
            User = get_user_model()
            user = User.objects.get(pk=subject_id)
        except Exception:
            return lines

        query_count = CypherQueryResult.objects.filter(
            created_at__gte=period_start,
            created_at__lt=period_end,
        ).count()

        if query_count:
            lines.append({
                "line_id": f"ravioli-query-{subject_id}-{period_start.date()}",
                "metric_code": "ravioli.cypher_query",
                "quantity": str(query_count),
                "unit": "query",
                "occurred_at": period_end.isoformat(),
                "source_type": "auth.User",
                "source_id": str(subject_id),
                "source_label": user.username,
                "description": f"{query_count} Cypher query execution(s) in period",
            })

        event_count = GraphChangeEvent.objects.filter(
            created_at__gte=period_start,
            created_at__lt=period_end,
        ).count()

        if event_count:
            lines.append({
                "line_id": f"ravioli-event-{subject_id}-{period_start.date()}",
                "metric_code": "ravioli.graph_event",
                "quantity": str(event_count),
                "unit": "event",
                "occurred_at": period_end.isoformat(),
                "source_type": "auth.User",
                "source_id": str(subject_id),
                "source_label": user.username,
                "description": f"{event_count} graph change event(s) in period",
            })

    return lines


def export_usage_statement(
    *,
    subject_type: str,
    subject_id: str,
    period_start: datetime,
    period_end: datetime,
    statement_id: str | None = None,
) -> dict:
    subject_label = f"{subject_type}:{subject_id}"
    try:
        if subject_type == "auth.User":
            from django.contrib.auth import get_user_model
            u = get_user_model().objects.get(pk=subject_id)
            subject_label = u.username
    except Exception:
        pass

    lines = _collect_lines(subject_type, subject_id, period_start, period_end)
    if not lines:
        raise ValidationError({"lines": "No ravioli usage events found for this subject in the given period."})

    data = build_usage_statement(
        statement_id=statement_id or str(uuid.uuid4()),
        source_app="ravioli",
        source_label="Ravioli Knowledge Graph",
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
    from django.core.files.base import ContentFile
    from toto.vault.models import VaultFile

    yaml_text = export_usage_statement_yaml(
        subject_type=subject_type,
        subject_id=subject_id,
        period_start=period_start,
        period_end=period_end,
    )
    safe_id = f"{subject_type.replace('.', '-')}-{subject_id}"
    filename = f"usage-statement-ravioli-{safe_id}-{period_start.date()}.yaml"
    if title is None:
        title = f"Ravioli Usage Statement {period_start.date()} / {period_end.date()} — {subject_type}:{subject_id}"

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
