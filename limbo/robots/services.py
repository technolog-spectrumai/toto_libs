from __future__ import annotations

from typing import Any

from django.db import transaction
from django.utils import timezone

from .models import (
    AssignmentStatus,
    ChargingSession,
    EventSeverity,
    MaintenanceStatus,
    MaintenanceTicket,
    MissionStatus,
    Robot,
    RobotAssignment,
    RobotCommand,
    RobotEvent,
    RobotMission,
    RobotStatus,
    RobotTelemetry,
)


@transaction.atomic
def commission_robot(robot: Robot, operator=None) -> Robot:
    robot.status = RobotStatus.IDLE
    robot.is_active = True
    robot.operator = operator or robot.operator
    robot.commissioned_at = robot.commissioned_at or timezone.now()
    robot.save(update_fields=["status", "is_active", "operator", "commissioned_at", "updated_at"])
    record_robot_event(robot, "commissioned", EventSeverity.INFO, "Robot commissioned for service.")
    return robot


@transaction.atomic
def retire_robot(robot: Robot, reason: str = "") -> Robot:
    robot.status = RobotStatus.RETIRED
    robot.is_active = False
    robot.retired_at = robot.retired_at or timezone.now()
    robot.save(update_fields=["status", "is_active", "retired_at", "updated_at"])

    RobotAssignment.objects.filter(
        robot=robot,
        status__in=[AssignmentStatus.QUEUED, AssignmentStatus.ACCEPTED, AssignmentStatus.RUNNING],
    ).update(status=AssignmentStatus.CANCELLED, failure_reason=reason)

    record_robot_event(robot, "retired", EventSeverity.WARNING, reason or "Robot retired.")
    return robot


@transaction.atomic
def assign_robot(mission: RobotMission, robot: Robot, role: str = "") -> RobotAssignment:
    if mission.community_id != robot.community_id:
        raise ValueError("Robot and mission must belong to the same community.")

    assignment, _ = RobotAssignment.objects.get_or_create(
        mission=mission,
        robot=robot,
        defaults={"role": role, "status": AssignmentStatus.QUEUED},
    )
    if role and assignment.role != role:
        assignment.role = role
        assignment.save(update_fields=["role"])
    return assignment


@transaction.atomic
def dispatch_mission(mission: RobotMission) -> RobotMission:
    mission.status = MissionStatus.DISPATCHED
    mission.save(update_fields=["status", "updated_at"])

    for assignment in mission.assignments.select_related("robot"):
        assignment.status = AssignmentStatus.ACCEPTED
        assignment.accepted_at = assignment.accepted_at or timezone.now()
        assignment.save(update_fields=["status", "accepted_at"])
        issue_command(assignment.robot, "mission.dispatch", {"mission_id": mission.pk}, mission=mission)

    return mission


@transaction.atomic
def start_assignment(assignment: RobotAssignment) -> RobotAssignment:
    now = timezone.now()
    assignment.status = AssignmentStatus.RUNNING
    assignment.started_at = assignment.started_at or now
    assignment.save(update_fields=["status", "started_at"])

    assignment.robot.status = RobotStatus.ACTIVE
    assignment.robot.save(update_fields=["status", "updated_at"])

    if assignment.mission.status in [MissionStatus.PLANNED, MissionStatus.DISPATCHED, MissionStatus.PAUSED]:
        assignment.mission.status = MissionStatus.RUNNING
        assignment.mission.started_at = assignment.mission.started_at or now
        assignment.mission.save(update_fields=["status", "started_at", "updated_at"])

    record_robot_event(assignment.robot, "assignment.started", EventSeverity.INFO, mission=assignment.mission)
    return assignment


@transaction.atomic
def complete_assignment(assignment: RobotAssignment, summary: dict[str, Any] | None = None) -> RobotAssignment:
    assignment.status = AssignmentStatus.COMPLETED
    assignment.completed_at = assignment.completed_at or timezone.now()
    if summary is not None:
        assignment.telemetry_summary = summary
    assignment.save(update_fields=["status", "completed_at", "telemetry_summary"])

    assignment.robot.status = RobotStatus.IDLE
    assignment.robot.save(update_fields=["status", "updated_at"])

    remaining = assignment.mission.assignments.exclude(status=AssignmentStatus.COMPLETED).exists()
    if not remaining:
        assignment.mission.status = MissionStatus.COMPLETED
        assignment.mission.completed_at = assignment.mission.completed_at or timezone.now()
        assignment.mission.save(update_fields=["status", "completed_at", "updated_at"])

    record_robot_event(assignment.robot, "assignment.completed", EventSeverity.INFO, mission=assignment.mission)
    return assignment


@transaction.atomic
def record_telemetry(robot: Robot, payload: dict[str, Any], mission: RobotMission | None = None) -> RobotTelemetry:
    telemetry = RobotTelemetry.objects.create(
        robot=robot,
        mission=mission,
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        altitude_m=payload.get("altitude_m"),
        speed_mps=payload.get("speed_mps"),
        heading_degrees=payload.get("heading_degrees"),
        battery_percent=payload.get("battery_percent"),
        battery_voltage=payload.get("battery_voltage"),
        temperature_c=payload.get("temperature_c"),
        signal_strength=payload.get("signal_strength"),
        cpu_percent=payload.get("cpu_percent"),
        memory_percent=payload.get("memory_percent"),
        payload=payload,
    )

    update_fields = ["last_seen_at", "updated_at"]
    robot.last_seen_at = telemetry.recorded_at
    if telemetry.battery_percent is not None:
        robot.battery_percent = telemetry.battery_percent
        update_fields.append("battery_percent")
    robot.save(update_fields=update_fields)
    return telemetry


def record_robot_event(
    robot: Robot,
    event_type: str,
    severity: str = EventSeverity.INFO,
    message: str = "",
    mission: RobotMission | None = None,
    data: dict[str, Any] | None = None,
    location=None,
) -> RobotEvent:
    return RobotEvent.objects.create(
        robot=robot,
        mission=mission,
        severity=severity,
        event_type=event_type,
        message=message,
        data=data or {},
        location=location,
    )


def issue_command(
    robot: Robot,
    command_type: str,
    payload: dict[str, Any] | None = None,
    mission: RobotMission | None = None,
    issued_by=None,
    idempotency_key: str = "",
) -> RobotCommand:
    if mission and mission.community_id != robot.community_id:
        raise ValueError("Robot and mission must belong to the same community.")

    if idempotency_key:
        existing = RobotCommand.objects.filter(robot=robot, idempotency_key=idempotency_key).first()
        if existing:
            return existing

    return RobotCommand.objects.create(
        robot=robot,
        mission=mission,
        command_type=command_type,
        payload=payload or {},
        issued_by=issued_by,
        idempotency_key=idempotency_key,
    )


def acknowledge_command(command: RobotCommand, response: dict[str, Any] | None = None) -> RobotCommand:
    command.acknowledged_at = command.acknowledged_at or timezone.now()
    if response:
        command.response = response
    command.save(update_fields=["acknowledged_at", "response"])
    return command


def complete_command(command: RobotCommand, success: bool, response: dict[str, Any] | None = None, error_message: str = "") -> RobotCommand:
    command.completed_at = command.completed_at or timezone.now()
    command.success = success
    if response is not None:
        command.response = response
    command.error_message = error_message
    command.save(update_fields=["completed_at", "success", "response", "error_message"])
    return command


@transaction.atomic
def open_maintenance_ticket(robot: Robot, title: str, description: str = "", severity: str = EventSeverity.WARNING, **kwargs) -> MaintenanceTicket:
    ticket = MaintenanceTicket.objects.create(
        robot=robot,
        title=title,
        description=description,
        severity=severity,
        **kwargs,
    )
    if severity in [EventSeverity.ERROR, EventSeverity.CRITICAL]:
        robot.status = RobotStatus.FAULTED
        robot.save(update_fields=["status", "updated_at"])
    record_robot_event(robot, "maintenance.opened", severity, title)
    return ticket


@transaction.atomic
def close_maintenance_ticket(ticket: MaintenanceTicket, notes: str = "") -> MaintenanceTicket:
    ticket.status = MaintenanceStatus.CLOSED
    ticket.closed_at = ticket.closed_at or timezone.now()
    ticket.resolved_at = ticket.resolved_at or ticket.closed_at
    ticket.save(update_fields=["status", "closed_at", "resolved_at"])

    if not ticket.robot.maintenance_tickets.exclude(pk=ticket.pk).filter(
        status__in=[MaintenanceStatus.OPEN, MaintenanceStatus.TRIAGED, MaintenanceStatus.IN_PROGRESS, MaintenanceStatus.WAITING_PARTS]
    ).exists():
        ticket.robot.status = RobotStatus.IDLE
        ticket.robot.save(update_fields=["status", "updated_at"])

    record_robot_event(ticket.robot, "maintenance.closed", EventSeverity.INFO, notes or ticket.title)
    return ticket


@transaction.atomic
def start_charging_session(robot: Robot, dock=None) -> ChargingSession:
    session = ChargingSession.objects.create(
        robot=robot,
        dock=dock,
        start_battery_percent=robot.battery_percent,
    )
    robot.status = RobotStatus.CHARGING
    robot.save(update_fields=["status", "updated_at"])
    record_robot_event(robot, "charging.started", EventSeverity.INFO, dock.name if dock else "Charging started")
    return session


@transaction.atomic
def end_charging_session(session: ChargingSession, end_battery_percent: int | None = None, energy_kwh=None) -> ChargingSession:
    session.ended_at = session.ended_at or timezone.now()
    if end_battery_percent is not None:
        session.end_battery_percent = end_battery_percent
    if energy_kwh is not None:
        session.energy_kwh = energy_kwh
    session.save(update_fields=["ended_at", "end_battery_percent", "energy_kwh"])

    robot = session.robot
    if session.end_battery_percent is not None:
        robot.battery_percent = session.end_battery_percent
    robot.status = RobotStatus.IDLE
    robot.save(update_fields=["battery_percent", "status", "updated_at"])
    record_robot_event(robot, "charging.ended", EventSeverity.INFO)
    return session
