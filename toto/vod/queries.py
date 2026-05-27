from __future__ import annotations

import json
from collections import defaultdict
from datetime import timedelta

from django.db.models import Count, Sum, Q
from django.db.models.functions import TruncDate
from django.utils import timezone

PALETTE = [
    "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#3b82f6",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
]


def vod_overview_stats() -> dict:
    from .models import VodCollection, VodVideo, VodPlaybackEvent

    total_seconds = VodPlaybackEvent.objects.aggregate(t=Sum("seconds_watched"))["t"] or 0
    return {
        "collections": VodCollection.objects.count(),
        "published": VodVideo.objects.filter(status=VodVideo.Status.PUBLISHED).count(),
        "hls_ready": VodVideo.objects.filter(hls_ready=True).count(),
        "plays": VodPlaybackEvent.objects.filter(event=VodPlaybackEvent.EventKind.PLAY).count(),
        "completions": VodPlaybackEvent.objects.filter(event=VodPlaybackEvent.EventKind.COMPLETE).count(),
        "hours_watched": round(total_seconds / 3600, 1),
        "unique_sessions": (
            VodPlaybackEvent.objects
            .exclude(session_key="")
            .values("session_key")
            .distinct()
            .count()
        ),
    }


def video_stats(video) -> dict:
    from .models import VodPlaybackEvent

    events = VodPlaybackEvent.objects.filter(video=video)
    plays = events.filter(event=VodPlaybackEvent.EventKind.PLAY).count()
    completions = events.filter(event=VodPlaybackEvent.EventKind.COMPLETE).count()
    seconds = events.aggregate(t=Sum("seconds_watched"))["t"] or 0
    unique = events.exclude(session_key="").values("session_key").distinct().count()
    return {
        "plays": plays,
        "completions": completions,
        "completion_rate": round(completions / plays * 100) if plays else 0,
        "minutes_watched": round(seconds / 60, 1),
        "unique_sessions": unique,
    }


def plays_by_day_chart_data(days: int = 30) -> str:
    from .models import VodPlaybackEvent

    since = timezone.now() - timedelta(days=days)
    qs = (
        VodPlaybackEvent.objects
        .filter(event=VodPlaybackEvent.EventKind.PLAY, created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    by_day = {row["day"].strftime("%Y-%m-%d"): row["count"] for row in qs}
    labels, data = [], []
    for i in range(days, -1, -1):
        d = (timezone.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        labels.append(d)
        data.append(by_day.get(d, 0))
    c = PALETTE[0]
    return json.dumps({
        "labels": labels,
        "datasets": [{
            "label": "Plays",
            "data": data,
            "borderColor": c,
            "backgroundColor": c + "33",
            "tension": 0.3,
            "pointRadius": 2,
            "fill": True,
        }],
    })


def top_videos_chart_data(limit: int = 8) -> str:
    from .models import VodPlaybackEvent

    qs = (
        VodPlaybackEvent.objects
        .filter(event=VodPlaybackEvent.EventKind.PLAY)
        .values("video__title")
        .annotate(plays=Count("id"))
        .order_by("-plays")[:limit]
    )
    rows = list(qs)
    if not rows:
        return json.dumps({"labels": [], "datasets": []})
    labels = [r["video__title"] for r in rows]
    data = [r["plays"] for r in rows]
    colors = [PALETTE[i % len(PALETTE)] for i in range(len(rows))]
    return json.dumps({
        "labels": labels,
        "datasets": [{
            "label": "Plays",
            "data": data,
            "backgroundColor": colors,
            "borderColor": colors,
            "borderWidth": 1,
            "borderRadius": 6,
        }],
    })
