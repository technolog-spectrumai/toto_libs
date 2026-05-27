from __future__ import annotations

import json
from datetime import timedelta

from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

PALETTE = ["#6366f1", "#f59e0b", "#10b981", "#ef4444", "#3b82f6", "#8b5cf6"]


def transcription_overview_stats() -> dict:
    from .models import TranscriptArtifact, TranscriptCollection, TranscriptEvent, TranscriptionJob, TranscriptSource

    seconds = TranscriptEvent.objects.aggregate(t=Sum("seconds_played"))["t"] or 0
    return {
        "collections": TranscriptCollection.objects.count(),
        "sources": TranscriptSource.objects.count(),
        "transcribed": TranscriptSource.objects.filter(status=TranscriptSource.Status.TRANSCRIBED).count(),
        "queued": TranscriptionJob.objects.filter(status=TranscriptionJob.Status.QUEUED).count(),
        "failed": TranscriptionJob.objects.filter(status=TranscriptionJob.Status.FAILED).count(),
        "exports": TranscriptArtifact.objects.count(),
        "hours": round(seconds / 3600, 1),
    }


def jobs_by_day_chart_data(days: int = 30) -> str:
    from .models import TranscriptionJob

    since = timezone.now() - timedelta(days=days)
    qs = (
        TranscriptionJob.objects
        .filter(status=TranscriptionJob.Status.SUCCESS, finished_at__gte=since)
        .annotate(day=TruncDate("finished_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"].strftime("%Y-%m-%d"): row["count"] for row in qs if row["day"]}
    labels, data = [], []
    for i in range(days, -1, -1):
        d = (timezone.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d)
        data.append(by_day.get(d, 0))
    c = PALETTE[0]
    return json.dumps({"labels": labels, "datasets": [{"label": "Transcriptions", "data": data, "borderColor": c, "backgroundColor": c + "33", "tension": 0.3, "pointRadius": 2, "fill": True}]})


def top_sources_chart_data(limit: int = 8) -> str:
    from .models import TranscriptEvent

    qs = (
        TranscriptEvent.objects
        .filter(event__in=[TranscriptEvent.EventKind.IMPRESSION, TranscriptEvent.EventKind.PLAY])
        .values("source__title")
        .annotate(views=Count("id"))
        .order_by("-views")[:limit]
    )
    labels = [row["source__title"] or "Untitled" for row in qs]
    data = [row["views"] for row in qs]
    return json.dumps({"labels": labels, "datasets": [{"label": "Views", "data": data, "backgroundColor": PALETTE[: max(1, len(labels))]}]})


def source_stats(source) -> dict:
    from .models import TranscriptEvent

    events = TranscriptEvent.objects.filter(source=source)
    plays = events.filter(event=TranscriptEvent.EventKind.PLAY).count()
    impressions = events.filter(event=TranscriptEvent.EventKind.IMPRESSION).count()
    exports = events.filter(event=TranscriptEvent.EventKind.EXPORT).count()
    seconds = events.aggregate(t=Sum("seconds_played"))["t"] or 0
    unique = events.exclude(session_key="").values("session_key").distinct().count()
    return {"impressions": impressions, "plays": plays, "exports": exports, "minutes_played": round(seconds / 60, 1), "unique_sessions": unique}
