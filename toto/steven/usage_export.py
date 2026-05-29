"""
Steven usage exporter — builds usage statements from AI agent runs.

Metric codes:
  ai.prompt_token      — input tokens consumed
  ai.completion_token  — output tokens generated
  ai.total_token       — total tokens (prompt + completion)
  ai.agent_run         — number of AgentRun executions
  ai.tool_call         — tool invocations within runs

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
            from toto.steven.models import AgentRun
            User = get_user_model()
            user = User.objects.get(pk=subject_id)
        except Exception:
            return lines

        from django.db.models import Sum

        runs_qs = AgentRun.objects.filter(
            created_at__gte=period_start,
            created_at__lt=period_end,
        )

        run_count = runs_qs.count()
        if run_count:
            lines.append({
                "line_id": f"steven-agent-run-{subject_id}-{period_start.date()}",
                "metric_code": "ai.agent_run",
                "quantity": str(run_count),
                "unit": "run",
                "occurred_at": period_end.isoformat(),
                "source_type": "auth.User",
                "source_id": str(subject_id),
                "source_label": user.username,
                "description": f"{run_count} agent run(s) in period",
            })

    elif subject_type == "steven.AgentProfile":
        try:
            from toto.steven.models import AgentProfile, AgentRun
        except Exception:
            return lines

        try:
            profile = AgentProfile.objects.get(pk=subject_id)
        except AgentProfile.DoesNotExist:
            return lines

        from django.db.models import Sum

        runs_qs = AgentRun.objects.filter(
            agent=profile,
            created_at__gte=period_start,
            created_at__lt=period_end,
        )

        run_count = runs_qs.count()
        if run_count:
            lines.append({
                "line_id": f"steven-agent-run-{subject_id}-{period_start.date()}",
                "metric_code": "ai.agent_run",
                "quantity": str(run_count),
                "unit": "run",
                "occurred_at": period_end.isoformat(),
                "source_type": "steven.AgentProfile",
                "source_id": str(subject_id),
                "source_label": str(profile),
                "description": f"{run_count} agent run(s) in period for {profile}",
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
        elif subject_type == "steven.AgentProfile":
            from toto.steven.models import AgentProfile
            p = AgentProfile.objects.get(pk=subject_id)
            subject_label = str(p)
    except Exception:
        pass

    lines = _collect_lines(subject_type, subject_id, period_start, period_end)
    if not lines:
        raise ValidationError({"lines": "No steven usage events found for this subject in the given period."})

    data = build_usage_statement(
        statement_id=statement_id or str(uuid.uuid4()),
        source_app="steven",
        source_label="Steven AI Agent",
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
    filename = f"usage-statement-steven-{safe_id}-{period_start.date()}.yaml"
    if title is None:
        title = f"Steven Usage Statement {period_start.date()} / {period_end.date()} — {subject_type}:{subject_id}"

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
