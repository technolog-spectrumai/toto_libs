from __future__ import annotations

try:
    from prometheus_client import Gauge

    robots_total = Gauge(
        "robots_total",
        "Total number of registered robots",
    )
    robots_by_status = Gauge(
        "robots_by_status",
        "Number of robots per status",
        ["status"],
    )
    robots_active = Gauge(
        "robots_active",
        "Number of active or returning robots",
    )
    robots_faulted = Gauge(
        "robots_faulted",
        "Number of faulted robots",
    )
    robots_avg_battery_percent = Gauge(
        "robots_avg_battery_percent",
        "Average battery percent across active robots",
    )
    robot_missions_total = Gauge(
        "robot_missions_total",
        "Total number of robot missions",
    )
    robot_missions_running = Gauge(
        "robot_missions_running",
        "Number of currently running robot missions",
    )
    robot_maintenance_open = Gauge(
        "robot_maintenance_open",
        "Number of open maintenance tickets",
    )
    robot_maintenance_critical = Gauge(
        "robot_maintenance_critical",
        "Number of critical open maintenance tickets",
    )

    PROMETHEUS_AVAILABLE = True

except ImportError:
    PROMETHEUS_AVAILABLE = False


def update_metrics() -> None:
    if not PROMETHEUS_AVAILABLE:
        return

    from django.db.models import Avg, Count, Q

    from .models import (
        EventSeverity,
        MaintenanceStatus,
        MaintenanceTicket,
        MissionStatus,
        Robot,
        RobotMission,
        RobotStatus,
    )

    robot_qs = Robot.objects.all()
    total = robot_qs.count()
    robots_total.set(total)

    status_counts = robot_qs.values("status").annotate(n=Count("id"))
    for row in status_counts:
        robots_by_status.labels(status=row["status"]).set(row["n"])

    robots_active.set(
        robot_qs.filter(status__in=[RobotStatus.ACTIVE, RobotStatus.RETURNING]).count()
    )
    robots_faulted.set(robot_qs.filter(status=RobotStatus.FAULTED).count())

    avg = robot_qs.filter(is_active=True).aggregate(avg=Avg("battery_percent"))["avg"]
    robots_avg_battery_percent.set(round(avg or 0, 2))

    mission_qs = RobotMission.objects.all()
    robot_missions_total.set(mission_qs.count())
    robot_missions_running.set(mission_qs.filter(status=MissionStatus.RUNNING).count())

    ticket_qs = MaintenanceTicket.objects.all()
    open_statuses = [MaintenanceStatus.OPEN, MaintenanceStatus.TRIAGED, MaintenanceStatus.IN_PROGRESS]
    robot_maintenance_open.set(ticket_qs.filter(status__in=open_statuses).count())
    robot_maintenance_critical.set(
        ticket_qs.filter(severity=EventSeverity.CRITICAL, status__in=open_statuses).count()
    )
