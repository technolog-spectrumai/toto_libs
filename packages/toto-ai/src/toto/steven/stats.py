"""The statistics tab's queries — metadata only, by construction.

The spec sentence this module enforces: **"Never record API keys or
unnecessary prompt contents."** No key exists anywhere on ``AiRun`` (the key
is a gervazy secret three tables away), and this module never SELECTS the
content columns — ``source_text``, ``instruction`` and ``result`` are not in
any ``values()`` call here, so no template fed from this module can leak them
even by accident. What statistics get is what a request *was*: when, who,
which surface and action, which agent and model answered, whether it worked,
how long it took, and what it cost in tokens.

Every ``.values().annotate()`` here ends in an explicit ``order_by`` —
``AiRun.Meta`` orders by ``-created_at``, and Django folds an ORDER BY column
into the GROUP BY, which would return one row per run rather than one per
bucket. The same trap antivirus documents in its statistics view.
"""

from __future__ import annotations

import json

from datetime import timedelta

from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import RunStatus

#: How many rows the recent-activity table shows. A pulse, not an archive —
#: the admin holds the full list.
RECENT_CAP = 25

#: Chart series colours — the antivirus statistics palette, same meanings.
_SUCCESS_COLOUR = "#10B981"
_FAILED_COLOUR = "#EF4444"
_TOKENS_COLOUR = "#6366F1"


def overview(qs) -> dict:
    """Totals: requests, tokens, success rate, average duration."""
    totals = qs.aggregate(runs=Count("id"), tokens=Sum("total_tokens"))
    # order_by() on the aggregate is load-bearing — see the module docstring.
    by_status = dict(qs.values_list("status").annotate(n=Count("id")).order_by())
    succeeded = by_status.get(RunStatus.SUCCESS, 0)
    failed = by_status.get(RunStatus.FAILED, 0)
    finished = succeeded + failed
    avg_ms = (qs.filter(status=RunStatus.SUCCESS, duration_ms__gt=0)
              .aggregate(avg=Avg("duration_ms"))["avg"])
    return {
        "runs": totals["runs"] or 0,
        "tokens": totals["tokens"] or 0,
        "succeeded": succeeded,
        "failed": failed,
        "success_percent": round(succeeded * 100 / finished) if finished else None,
        "avg_duration_ms": round(avg_ms) if avg_ms else None,
    }


def _grouped(qs, field: str, label: str) -> list:
    """Requests and tokens grouped by one metadata column, busiest first."""
    rows = (qs.values(field)
            .annotate(n=Count("id"), tokens=Sum("total_tokens"))
            .order_by("-n"))
    return [{"label": row[field] or label, "runs": row["n"],
             "tokens": row["tokens"] or 0} for row in rows]


def by_agent(qs) -> list:
    return _grouped(qs, "agent_label", "(no agent)")


def by_model(qs) -> list:
    return _grouped(qs, "model_used", "(unknown)")


def by_surface(qs) -> list:
    """Per surface/action pair — which app asked, and for what."""
    rows = (qs.values("surface", "action")
            .annotate(n=Count("id"), tokens=Sum("total_tokens"))
            .order_by("-n"))
    return [{"label": f"{row['surface'] or '?'} / {row['action'] or '?'}",
             "runs": row["n"], "tokens": row["tokens"] or 0} for row in rows]


def requests_chart_json(qs, *, days: int = 30) -> str:
    """Requests per day, stacked success/failed. "" when empty.

    The antivirus activity chart restated: zero-padded calendar so three
    active days apart read as a month, trailing ``order_by("day")`` against
    the Meta-ordering GROUP BY trap.
    """
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)

    counted: dict = {}
    rows = (qs.filter(created_at__date__gte=since)
            .annotate(day=TruncDate("created_at"))
            .values("day", "status")
            .annotate(n=Count("id"))
            .order_by("day"))
    for row in rows:
        counted.setdefault(row["status"], {})[row["day"]] = row["n"]

    if not counted:
        return ""

    labels = [(since + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
    datasets = []
    for status, label, colour in ((RunStatus.SUCCESS, "Answered", _SUCCESS_COLOUR),
                                  (RunStatus.FAILED, "Failed", _FAILED_COLOUR)):
        by_day = counted.get(status)
        if not by_day:
            continue
        datasets.append({
            "label": label,
            "data": [by_day.get(since + timedelta(days=i), 0)
                     for i in range(days)],
            "backgroundColor": colour,
        })

    return json.dumps({
        "chart_type": "bar",
        "labels": labels,
        "datasets": datasets,
        "options": {"scales": {"x": {"stacked": True},
                               "y": {"stacked": True, "beginAtZero": True}}},
    })


def tokens_chart_json(qs, *, days: int = 30) -> str:
    """Tokens per day, one bar series. "" when nothing was spent."""
    today = timezone.localdate()
    since = today - timedelta(days=days - 1)

    rows = (qs.filter(created_at__date__gte=since, total_tokens__gt=0)
            .annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(tokens=Sum("total_tokens"))
            .order_by("day"))
    by_day = {row["day"]: row["tokens"] for row in rows}
    if not by_day:
        return ""

    labels = [(since + timedelta(days=i)).strftime("%m-%d") for i in range(days)]
    return json.dumps({
        "chart_type": "bar",
        "labels": labels,
        "datasets": [{
            "label": "Tokens",
            "data": [by_day.get(since + timedelta(days=i), 0)
                     for i in range(days)],
            "backgroundColor": _TOKENS_COLOUR,
        }],
        "options": {"scales": {"y": {"beginAtZero": True}}},
    })


def recent(qs, *, limit: int = RECENT_CAP) -> list:
    """The recent-activity rows. Metadata columns ONLY — content is never
    selected, so it is structurally unreachable from the template."""
    return list(qs.values(
        "created_at", "owner__username", "surface", "action", "agent_label",
        "model_used", "status", "total_tokens", "duration_ms",
    ).order_by("-created_at")[:limit])


def context_for(qs, *, staff: bool) -> dict:
    """Everything one statistics rendering needs.

    ``staff`` decides whether the owner column is offered (the aggregate page
    shows who; a user's own page has no reason to repeat their name) and
    whether the charts are built — the console card is numbers and a table.
    """
    context = {
        "stats": overview(qs),
        "stats_recent": recent(qs),
        "stats_show_owner": staff,
    }
    if staff:
        context.update({
            "stats_by_agent": by_agent(qs),
            "stats_by_model": by_model(qs),
            "stats_by_surface": by_surface(qs),
            "requests_chart_json": requests_chart_json(qs),
            "tokens_chart_json": tokens_chart_json(qs),
        })
    return context
