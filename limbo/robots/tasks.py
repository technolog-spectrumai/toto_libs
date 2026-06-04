from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from .models import EventSeverity, Robot, RobotStatus
from .services import record_robot_event


@shared_task
def mark_stale_robots_offline(minutes: int = 10) -> int:
    """Mark active robots offline when they have not reported telemetry recently."""
    cutoff = timezone.now() - timedelta(minutes=minutes)
    qs = Robot.objects.filter(
        is_active=True,
        last_seen_at__lt=cutoff,
    ).exclude(status__in=[RobotStatus.OFFLINE, RobotStatus.RETIRED, RobotStatus.MAINTENANCE])

    count = 0
    for robot in qs:
        robot.status = RobotStatus.OFFLINE
        robot.save(update_fields=["status", "updated_at"])
        record_robot_event(robot, "robot.offline", EventSeverity.WARNING, f"No telemetry since {robot.last_seen_at}.")
        count += 1
    return count


@shared_task
def detect_low_battery(threshold: int = 20) -> int:
    """Create warning events for robots whose battery is below the threshold."""
    qs = Robot.objects.filter(is_active=True, battery_percent__lt=threshold).exclude(status=RobotStatus.RETIRED)
    count = 0
    for robot in qs:
        record_robot_event(
            robot,
            "battery.low",
            EventSeverity.WARNING,
            f"Battery is {robot.battery_percent}%.",
            data={"battery_percent": robot.battery_percent, "threshold": threshold},
        )
        count += 1
    return count


@shared_task
def dispatch_pending_commands(limit: int = 100) -> int:
    """
    Placeholder for future hardware integration.

    For now the app assumes manual operation. Later, this task can send unsent
    RobotCommand records to MQTT, ROS2, MAVLink, vendor HTTP APIs, or an edge gateway.
    """
    return 0
