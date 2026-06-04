from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class RobotKind(models.TextChoices):
    GROUND = "ground", "Ground Robot"
    AERIAL = "aerial", "Aerial Drone"
    MARINE = "marine", "Marine Robot"
    UNDERWATER = "underwater", "Underwater Robot"
    HUMANOID = "humanoid", "Humanoid"
    ARM = "arm", "Robotic Arm"
    SENSOR = "sensor", "Sensor Platform"
    OTHER = "other", "Other"


class RobotStatus(models.TextChoices):
    PROVISIONING = "provisioning", "Provisioning"
    IDLE = "idle", "Idle"
    ACTIVE = "active", "Active"
    RETURNING = "returning", "Returning"
    CHARGING = "charging", "Charging"
    MAINTENANCE = "maintenance", "Maintenance"
    OFFLINE = "offline", "Offline"
    FAULTED = "faulted", "Faulted"
    RETIRED = "retired", "Retired"


class MissionStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    PLANNED = "planned", "Planned"
    DISPATCHED = "dispatched", "Dispatched"
    RUNNING = "running", "Running"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class AssignmentStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    ACCEPTED = "accepted", "Accepted"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"
    FAILED = "failed", "Failed"


class EventSeverity(models.TextChoices):
    INFO = "info", "Info"
    WARNING = "warning", "Warning"
    ERROR = "error", "Error"
    CRITICAL = "critical", "Critical"


class MaintenanceStatus(models.TextChoices):
    OPEN = "open", "Open"
    TRIAGED = "triaged", "Triaged"
    IN_PROGRESS = "in_progress", "In Progress"
    WAITING_PARTS = "waiting_parts", "Waiting for Parts"
    RESOLVED = "resolved", "Resolved"
    CLOSED = "closed", "Closed"


class RobotModel(models.Model):
    """Manufacturer/model template shared by many physical robots."""

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    manufacturer = models.CharField(max_length=160)
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180, unique=True)
    kind = models.CharField(max_length=32, choices=RobotKind.choices)

    description = models.TextField(blank=True)
    payload_capacity_kg = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    max_speed_mps = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    battery_capacity_wh = models.PositiveIntegerField(null=True, blank=True)
    nominal_runtime_minutes = models.PositiveIntegerField(null=True, blank=True)

    supported_capabilities = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["manufacturer", "name"]
        constraints = [
            models.UniqueConstraint(fields=["manufacturer", "name"], name="robots_unique_manufacturer_model"),
        ]

    def __str__(self) -> str:
        return f"{self.manufacturer} {self.name}"


class RobotCapability(models.Model):
    """Normalized capability registry: delivery, mapping, inspection, patrol, etc."""

    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "robot capabilities"

    def __str__(self) -> str:
        return self.name


class Robot(models.Model):
    """A concrete robot in the fleet."""

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="robots",
    )
    model = models.ForeignKey(
        RobotModel,
        on_delete=models.PROTECT,
        related_name="robots",
    )

    name = models.CharField(max_length=160)
    callsign = models.SlugField(max_length=80)
    serial_number = models.CharField(max_length=160, blank=True)
    inventory_object = models.OneToOneField(
        "inventory.RealWorldObject",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot",
        help_text="Optional physical asset registry link.",
    )

    status = models.CharField(
        max_length=32,
        choices=RobotStatus.choices,
        default=RobotStatus.PROVISIONING,
        db_index=True,
    )
    capabilities = models.ManyToManyField(
        RobotCapability,
        blank=True,
        related_name="robots",
    )

    operator = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="operated_robots",
    )
    home_base = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="based_robots",
    )
    current_location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robots_currently_here",
    )

    firmware_version = models.CharField(max_length=80, blank=True)
    hardware_revision = models.CharField(max_length=80, blank=True)

    battery_percent = models.PositiveSmallIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    commissioned_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    is_autonomous = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["community", "callsign"]
        constraints = [
            models.UniqueConstraint(fields=["community", "callsign"], name="robots_unique_community_callsign"),
            models.UniqueConstraint(
                fields=["community", "serial_number"],
                condition=~Q(serial_number=""),
                name="robots_unique_nonempty_serial_per_community",
            ),
        ]
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["last_seen_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.callsign} — {self.name}"

    def clean(self) -> None:
        if self.battery_percent > 100:
            raise ValidationError({"battery_percent": "Battery percent cannot exceed 100."})

        if self.retired_at and self.status != RobotStatus.RETIRED:
            raise ValidationError({"status": "Robot with retired_at set should have retired status."})


class Fleet(models.Model):
    """Operational grouping of robots inside a community."""

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="robot_fleets",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)

    description = models.TextField(blank=True)
    manager = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="managed_robot_fleets",
    )
    robots = models.ManyToManyField(
        Robot,
        through="FleetMembership",
        related_name="fleets",
        blank=True,
    )

    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["community", "name"]
        constraints = [
            models.UniqueConstraint(fields=["community", "slug"], name="robots_unique_fleet_slug_per_community"),
        ]

    def __str__(self) -> str:
        return self.name


class FleetMembership(models.Model):
    fleet = models.ForeignKey(Fleet, on_delete=models.CASCADE)
    robot = models.ForeignKey(Robot, on_delete=models.CASCADE)
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)
    role = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["fleet", "robot"]
        constraints = [
            models.UniqueConstraint(fields=["fleet", "robot"], name="robots_unique_robot_fleet_membership"),
        ]

    def clean(self) -> None:
        if self.robot_id and self.fleet_id and self.robot.community_id != self.fleet.community_id:
            raise ValidationError("Robot and fleet must belong to the same community.")

    def __str__(self) -> str:
        return f"{self.robot} in {self.fleet}"


class RobotMission(models.Model):
    """Mission/work order that can be assigned to one or more robots."""

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="robot_missions",
    )
    fleet = models.ForeignKey(
        Fleet,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="missions",
    )

    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=32,
        choices=MissionStatus.choices,
        default=MissionStatus.DRAFT,
        db_index=True,
    )

    requested_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requested_robot_missions",
    )
    supervisor = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supervised_robot_missions",
    )

    origin = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_mission_origins",
    )
    destination = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_mission_destinations",
    )
    route = models.ForeignKey(
        "locations.Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_missions",
    )

    scheduled_start_at = models.DateTimeField(null=True, blank=True)
    scheduled_end_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    priority = models.PositiveSmallIntegerField(default=5)
    payload = models.JSONField(default=dict, blank=True)
    constraints = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)

    detection = models.ForeignKey(
        "detections.Detection",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_missions",
        help_text="Optional incident/threat this mission mitigates.",
    )
    kanban_task = models.ForeignKey(
        "kanban.Task",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_missions",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [
            ("dispatch_robot_mission", "Can dispatch robot missions"),
            ("issue_robot_command", "Can issue robot commands"),
            ("view_robot_telemetry", "Can view robot telemetry"),
            ("manage_robot_maintenance", "Can manage robot maintenance"),
            ("install_robot_firmware", "Can install robot firmware"),
        ]
        indexes = [
            models.Index(fields=["community", "status"]),
            models.Index(fields=["scheduled_start_at"]),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self) -> None:
        if self.fleet_id and self.fleet.community_id != self.community_id:
            raise ValidationError({"fleet": "Fleet must belong to the same community as the mission."})

        if self.scheduled_start_at and self.scheduled_end_at:
            if self.scheduled_end_at <= self.scheduled_start_at:
                raise ValidationError({"scheduled_end_at": "Scheduled end must be after scheduled start."})


class MissionWaypoint(models.Model):
    mission = models.ForeignKey(
        RobotMission,
        on_delete=models.CASCADE,
        related_name="waypoints",
    )
    address = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_waypoints",
    )

    order = models.PositiveIntegerField()
    label = models.CharField(max_length=160, blank=True)
    instruction = models.TextField(blank=True)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    altitude_m = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    hold_seconds = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["mission", "order"]
        constraints = [
            models.UniqueConstraint(fields=["mission", "order"], name="robots_unique_waypoint_order_per_mission"),
        ]

    def __str__(self) -> str:
        return f"{self.mission} waypoint {self.order}"


class RobotAssignment(models.Model):
    """A robot's participation in a mission."""

    mission = models.ForeignKey(
        RobotMission,
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="assignments",
    )

    status = models.CharField(
        max_length=32,
        choices=AssignmentStatus.choices,
        default=AssignmentStatus.QUEUED,
        db_index=True,
    )
    role = models.CharField(max_length=80, blank=True)

    accepted_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    failure_reason = models.TextField(blank=True)
    telemetry_summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["mission", "robot"]
        constraints = [
            models.UniqueConstraint(fields=["mission", "robot"], name="robots_unique_robot_assignment_per_mission"),
        ]
        indexes = [
            models.Index(fields=["robot", "status"]),
            models.Index(fields=["mission", "status"]),
        ]

    def clean(self) -> None:
        if self.robot_id and self.mission_id:
            if self.robot.community_id != self.mission.community_id:
                raise ValidationError("Robot and mission must belong to the same community.")

    def __str__(self) -> str:
        return f"{self.robot} → {self.mission}"


class RobotTelemetry(models.Model):
    """
    Append-only time-series telemetry snapshot.

    For high-volume fleets, partition this table by time or move raw telemetry
    to TimescaleDB/ClickHouse while keeping summaries here.
    """

    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="telemetry",
    )
    mission = models.ForeignKey(
        RobotMission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="telemetry",
    )

    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    altitude_m = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    speed_mps = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    heading_degrees = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    battery_voltage = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    temperature_c = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)

    signal_strength = models.IntegerField(null=True, blank=True)
    cpu_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    memory_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["robot", "-recorded_at"]),
            models.Index(fields=["mission", "-recorded_at"]),
        ]

    def clean(self) -> None:
        if self.battery_percent is not None and self.battery_percent > 100:
            raise ValidationError({"battery_percent": "Battery percent cannot exceed 100."})

    def __str__(self) -> str:
        return f"{self.robot} telemetry @ {self.recorded_at:%Y-%m-%d %H:%M:%S}"


class RobotEvent(models.Model):
    """Append-only operational event log."""

    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="events",
    )
    mission = models.ForeignKey(
        RobotMission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )

    severity = models.CharField(
        max_length=16,
        choices=EventSeverity.choices,
        default=EventSeverity.INFO,
        db_index=True,
    )
    event_type = models.CharField(max_length=80, db_index=True)
    message = models.TextField(blank=True)

    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_events",
    )

    data = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    acknowledged_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="acknowledged_robot_events",
    )
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["robot", "severity", "-occurred_at"]),
            models.Index(fields=["event_type"]),
        ]

    def __str__(self) -> str:
        return f"{self.robot} {self.event_type} ({self.severity})"


class ChargingDock(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="charging_docks",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)

    location = models.ForeignKey(
        "locations.Address",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charging_docks",
    )

    supported_models = models.ManyToManyField(
        RobotModel,
        blank=True,
        related_name="charging_docks",
    )

    power_kw = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True)
    slot_count = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["community", "name"]
        constraints = [
            models.UniqueConstraint(fields=["community", "slug"], name="robots_unique_dock_slug_per_community"),
        ]

    def __str__(self) -> str:
        return self.name


class ChargingSession(models.Model):
    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="charging_sessions",
    )
    dock = models.ForeignKey(
        ChargingDock,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="charging_sessions",
    )

    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    start_battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    end_battery_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    energy_kwh = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["robot", "-started_at"]),
            models.Index(fields=["dock", "-started_at"]),
        ]

    def clean(self) -> None:
        for field in ["start_battery_percent", "end_battery_percent"]:
            value = getattr(self, field)
            if value is not None and value > 100:
                raise ValidationError({field: "Battery percent cannot exceed 100."})

        if self.ended_at and self.ended_at <= self.started_at:
            raise ValidationError({"ended_at": "Charging session must end after it starts."})

    def __str__(self) -> str:
        return f"{self.robot} charging @ {self.started_at:%Y-%m-%d %H:%M}"


class RobotComponent(models.Model):
    """Replaceable component mounted on a robot."""

    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="components",
    )

    name = models.CharField(max_length=160)
    component_type = models.CharField(max_length=80)
    serial_number = models.CharField(max_length=160, blank=True)

    installed_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)

    expected_lifetime_hours = models.PositiveIntegerField(null=True, blank=True)
    runtime_hours = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["robot", "component_type", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["robot", "serial_number"],
                condition=~Q(serial_number=""),
                name="robots_unique_component_serial_per_robot",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.robot} / {self.name}"


class ComponentReading(models.Model):
    component = models.ForeignKey(
        RobotComponent,
        on_delete=models.CASCADE,
        related_name="readings",
    )
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    metric = models.CharField(max_length=80)
    value = models.DecimalField(max_digits=14, decimal_places=4)
    unit = models.CharField(max_length=32, blank=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-recorded_at"]
        indexes = [
            models.Index(fields=["component", "metric", "-recorded_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.component} {self.metric}={self.value}{self.unit}"


class MaintenanceTicket(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)

    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="maintenance_tickets",
    )
    component = models.ForeignKey(
        RobotComponent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="maintenance_tickets",
    )

    title = models.CharField(max_length=180)
    description = models.TextField(blank=True)

    status = models.CharField(
        max_length=32,
        choices=MaintenanceStatus.choices,
        default=MaintenanceStatus.OPEN,
        db_index=True,
    )
    severity = models.CharField(
        max_length=16,
        choices=EventSeverity.choices,
        default=EventSeverity.WARNING,
    )

    reported_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reported_robot_maintenance_tickets",
    )
    assigned_to = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_robot_maintenance_tickets",
    )

    opened_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    cost_base_units = models.BigIntegerField(null=True, blank=True)
    cost_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_maintenance_costs",
    )

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["robot", "status"]),
            models.Index(fields=["severity", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.robot} — {self.title}"


class MaintenanceAction(models.Model):
    ticket = models.ForeignKey(
        MaintenanceTicket,
        on_delete=models.CASCADE,
        related_name="actions",
    )

    performed_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_maintenance_actions",
    )

    action_type = models.CharField(max_length=80)
    notes = models.TextField(blank=True)
    performed_at = models.DateTimeField(default=timezone.now)

    parts_used = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-performed_at"]

    def __str__(self) -> str:
        return f"{self.ticket} / {self.action_type}"


class FirmwareRelease(models.Model):
    model = models.ForeignKey(
        RobotModel,
        on_delete=models.CASCADE,
        related_name="firmware_releases",
    )

    version = models.CharField(max_length=80)
    release_notes = models.TextField(blank=True)
    artifact_url = models.URLField(blank=True)
    checksum = models.CharField(max_length=128, blank=True)

    is_required = models.BooleanField(default=False)
    released_at = models.DateTimeField(default=timezone.now)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["model", "-released_at"]
        constraints = [
            models.UniqueConstraint(fields=["model", "version"], name="robots_unique_firmware_release_per_model"),
        ]

    def __str__(self) -> str:
        return f"{self.model} firmware {self.version}"


class FirmwareInstallation(models.Model):
    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="firmware_installations",
    )
    release = models.ForeignKey(
        FirmwareRelease,
        on_delete=models.PROTECT,
        related_name="installations",
    )

    installed_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="robot_firmware_installations",
    )

    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    success = models.BooleanField(default=False)
    log = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(fields=["robot", "release"], name="robots_unique_firmware_installation"),
        ]

    def clean(self) -> None:
        if self.robot_id and self.release_id:
            if self.robot.model_id != self.release.model_id:
                raise ValidationError("Firmware release must match the robot model.")

    def __str__(self) -> str:
        return f"{self.robot} → {self.release}"


class RobotCommand(models.Model):
    """
    Command queue/audit table.

    The actual dispatch should happen in a service or Celery task, not in model.save().
    """

    robot = models.ForeignKey(
        Robot,
        on_delete=models.CASCADE,
        related_name="commands",
    )
    mission = models.ForeignKey(
        RobotMission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="commands",
    )

    command_type = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)

    issued_by = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_robot_commands",
    )

    issued_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    success = models.BooleanField(null=True, blank=True)
    response = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    idempotency_key = models.CharField(max_length=120, blank=True, db_index=True)

    class Meta:
        ordering = ["-issued_at"]
        indexes = [
            models.Index(fields=["robot", "-issued_at"]),
            models.Index(fields=["command_type"]),
            models.Index(fields=["idempotency_key"]),
        ]

    def clean(self) -> None:
        if self.robot_id and self.mission_id:
            if self.robot.community_id != self.mission.community_id:
                raise ValidationError("Robot and mission must belong to the same community.")

    def __str__(self) -> str:
        return f"{self.robot} command {self.command_type}"
