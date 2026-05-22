from __future__ import annotations

import json

from django.db.models import Count, Q

from .models import Magistrate, MagistrateReport, MagistrateRole


def overview_stats() -> dict:
    return {
        "total_seats": Magistrate.objects.count(),
        "active_seats": Magistrate.objects.filter(status="active").count(),
        "suspended_seats": Magistrate.objects.filter(status="suspended").count(),
        "term_ended_seats": Magistrate.objects.filter(status="term_ended").count(),
        "total_roles": MagistrateRole.objects.count(),
        "total_reports": MagistrateReport.objects.count(),
        "pending_reports": MagistrateReport.objects.filter(status="submitted").count(),
        "acknowledged_reports": MagistrateReport.objects.filter(status="acknowledged").count(),
        "communities_count": (
            Magistrate.objects.filter(status="active")
            .values("community").distinct().count()
        ),
    }


def seats_by_role_chart_data() -> str:
    rows = list(
        MagistrateRole.objects
        .annotate(active_count=Count("holders", filter=Q(holders__status="active")))
        .order_by("order", "name")
        .values("name", "active_count")
    )
    return json.dumps({
        "labels": [r["name"] for r in rows],
        "datasets": [{
            "label": "Active seats",
            "data": [r["active_count"] for r in rows],
            "backgroundColor": "rgba(99, 102, 241, 0.75)",
            "borderRadius": 4,
        }],
    })


def seats_by_status_chart_data() -> str:
    statuses = [
        ("active",     "Active",     "rgba(34, 197, 94, 0.75)"),
        ("suspended",  "Suspended",  "rgba(234, 179, 8, 0.75)"),
        ("impeached",  "Impeached",  "rgba(239, 68, 68, 0.75)"),
        ("term_ended", "Term Ended", "rgba(100, 116, 139, 0.75)"),
    ]
    data = [Magistrate.objects.filter(status=s).count() for s, _, _ in statuses]
    return json.dumps({
        "labels": [label for _, label, _ in statuses],
        "datasets": [{
            "data": data,
            "backgroundColor": [color for _, _, color in statuses],
        }],
    })


def reports_by_status_chart_data() -> str:
    statuses = [
        ("draft",        "Draft",        "rgba(100, 116, 139, 0.75)"),
        ("submitted",    "Submitted",    "rgba(234, 179, 8, 0.75)"),
        ("acknowledged", "Acknowledged", "rgba(34, 197, 94, 0.75)"),
    ]
    data = [MagistrateReport.objects.filter(status=s).count() for s, _, _ in statuses]
    return json.dumps({
        "labels": [label for _, label, _ in statuses],
        "datasets": [{
            "label": "Reports",
            "data": data,
            "backgroundColor": [color for _, _, color in statuses],
            "borderRadius": 4,
        }],
    })
