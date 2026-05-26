from __future__ import annotations

from collections import defaultdict

from django.db.models import Avg, Count, Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from datetime import timedelta

from .models import (
    ChargingSession,
    MaintenanceStatus,
    MissionStatus,
    Robot,
    RobotEvent,
    RobotMission,
    RobotStatus,
    RobotTelemetry,
    MaintenanceTicket,
    EventSeverity,
)

PALETTE = [
    "#6366f1", "#f59e0b", "#10b981", "#ef4444", "#3b82f6",
    "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16",
]

STATUS_COLORS = {
    RobotStatus.IDLE: "#10b981",
    RobotStatus.ACTIVE: "#6366f1",
    RobotStatus.RETURNING: "#3b82f6",
    RobotStatus.CHARGING: "#f59e0b",
    RobotStatus.MAINTENANCE: "#f97316",
    RobotStatus.OFFLINE: "#6b7280",
    RobotStatus.FAULTED: "#ef4444",
    RobotStatus.PROVISIONING: "#8b5cf6",
    RobotStatus.RETIRED: "#374151",
}

MISSION_STATUS_COLORS = {
    MissionStatus.DRAFT: "#6b7280",
    MissionStatus.PLANNED: "#3b82f6",
    MissionStatus.DISPATCHED: "#8b5cf6",
    MissionStatus.RUNNING: "#6366f1",
    MissionStatus.PAUSED: "#f59e0b",
    MissionStatus.COMPLETED: "#10b981",
    MissionStatus.CANCELLED: "#9ca3af",
    MissionStatus.FAILED: "#ef4444",
}


def overview_stats() -> dict:
    robots = Robot.objects.all()
    missions = RobotMission.objects.all()
    tickets = MaintenanceTicket.objects.all()

    return {
        "total_robots": robots.count(),
        "active_robots": robots.filter(
            status__in=[RobotStatus.ACTIVE, RobotStatus.RETURNING]
        ).count(),
        "idle_robots": robots.filter(status=RobotStatus.IDLE).count(),
        "charging_robots": robots.filter(status=RobotStatus.CHARGING).count(),
        "offline_robots": robots.filter(status=RobotStatus.OFFLINE).count(),
        "faulted_robots": robots.filter(status=RobotStatus.FAULTED).count(),
        "maintenance_robots": robots.filter(status=RobotStatus.MAINTENANCE).count(),
        "provisioning_robots": robots.filter(status=RobotStatus.PROVISIONING).count(),
        "total_missions": missions.count(),
        "running_missions": missions.filter(status=MissionStatus.RUNNING).count(),
        "completed_missions": missions.filter(status=MissionStatus.COMPLETED).count(),
        "failed_missions": missions.filter(status=MissionStatus.FAILED).count(),
        "open_tickets": tickets.filter(
            status__in=[MaintenanceStatus.OPEN, MaintenanceStatus.TRIAGED, MaintenanceStatus.IN_PROGRESS]
        ).count(),
        "critical_tickets": tickets.filter(
            severity=EventSeverity.CRITICAL,
            status__in=[MaintenanceStatus.OPEN, MaintenanceStatus.TRIAGED, MaintenanceStatus.IN_PROGRESS],
        ).count(),
        "avg_battery": round(
            robots.filter(is_active=True).aggregate(avg=Avg("battery_percent"))["avg"] or 0, 1
        ),
        "autonomous_robots": robots.filter(is_autonomous=True, is_active=True).count(),
        "total_telemetry": RobotTelemetry.objects.count(),
        "total_events": RobotEvent.objects.count(),
    }


def robot_status_chart_data() -> dict:
    rows = (
        Robot.objects
        .values("status")
        .annotate(count=Count("id"))
        .order_by("status")
    )
    labels = [r["status"] for r in rows]
    counts = [r["count"] for r in rows]
    colors = [STATUS_COLORS.get(s, PALETTE[i % len(PALETTE)]) for i, s in enumerate(labels)]
    return {
        "labels": labels,
        "datasets": [{
            "data": counts,
            "backgroundColor": colors,
            "borderColor": colors,
            "borderWidth": 1,
        }],
    }


def mission_status_chart_data() -> dict:
    rows = (
        RobotMission.objects
        .values("status")
        .annotate(count=Count("id"))
        .order_by("status")
    )
    labels = [r["status"] for r in rows]
    counts = [r["count"] for r in rows]
    colors = [MISSION_STATUS_COLORS.get(s, PALETTE[i % len(PALETTE)]) for i, s in enumerate(labels)]
    return {
        "labels": labels,
        "datasets": [{
            "data": counts,
            "backgroundColor": colors,
            "borderColor": colors,
            "borderWidth": 1,
        }],
    }


def missions_over_time_chart_data(days: int = 30) -> dict:
    since = timezone.now() - timedelta(days=days)
    rows = (
        RobotMission.objects
        .filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    labels = [str(r["day"]) for r in rows]
    counts = [r["count"] for r in rows]
    return {
        "labels": labels,
        "datasets": [{
            "label": "Missions created",
            "data": counts,
            "borderColor": "#6366f1",
            "backgroundColor": "#6366f133",
            "tension": 0.3,
            "fill": True,
            "pointRadius": 4,
        }],
    }


def battery_distribution_chart_data() -> dict:
    buckets = [
        ("0–20%", Q(battery_percent__lte=20)),
        ("21–40%", Q(battery_percent__gt=20, battery_percent__lte=40)),
        ("41–60%", Q(battery_percent__gt=40, battery_percent__lte=60)),
        ("61–80%", Q(battery_percent__gt=60, battery_percent__lte=80)),
        ("81–100%", Q(battery_percent__gt=80)),
    ]
    labels = [b[0] for b in buckets]
    counts = [Robot.objects.filter(is_active=True).filter(b[1]).count() for b in buckets]
    colors = ["#ef4444", "#f97316", "#f59e0b", "#3b82f6", "#10b981"]
    return {
        "labels": labels,
        "datasets": [{
            "data": counts,
            "backgroundColor": colors,
            "borderColor": colors,
            "borderWidth": 1,
        }],
    }


def events_by_severity_chart_data(days: int = 7) -> dict:
    since = timezone.now() - timedelta(days=days)
    rows = (
        RobotEvent.objects
        .filter(occurred_at__gte=since)
        .annotate(day=TruncDate("occurred_at"))
        .values("day", "severity")
        .annotate(count=Count("id"))
        .order_by("day", "severity")
    )

    all_days = sorted({str(r["day"]) for r in rows})
    severity_data: dict[str, dict[str, int]] = defaultdict(dict)
    for r in rows:
        severity_data[r["severity"]][str(r["day"])] = r["count"]

    severity_colors = {
        EventSeverity.INFO: "#6366f1",
        EventSeverity.WARNING: "#f59e0b",
        EventSeverity.ERROR: "#f97316",
        EventSeverity.CRITICAL: "#ef4444",
    }
    datasets = []
    for sev, color in severity_colors.items():
        day_map = severity_data.get(sev, {})
        datasets.append({
            "label": sev.title(),
            "data": [day_map.get(d, 0) for d in all_days],
            "backgroundColor": color + "bb",
            "borderColor": color,
            "borderWidth": 1,
        })

    return {"labels": all_days, "datasets": datasets}
