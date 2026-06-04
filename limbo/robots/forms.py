from __future__ import annotations

from django import forms

from .models import (
    ChargingDock,
    ChargingSession,
    FirmwareInstallation,
    FirmwareRelease,
    Fleet,
    FleetMembership,
    MaintenanceAction,
    MaintenanceTicket,
    MissionWaypoint,
    Robot,
    RobotAssignment,
    RobotCapability,
    RobotCommand,
    RobotComponent,
    RobotEvent,
    RobotMission,
    RobotModel,
    RobotTelemetry,
)

BASE_INPUT_CLASS = (
    "w-full rounded-lg border px-3 py-2 text-sm outline-none "
    "transition focus:ring-2 focus:ring-current/20"
)
BASE_SELECT_CLASS = BASE_INPUT_CLASS
BASE_TEXTAREA_CLASS = BASE_INPUT_CLASS + " min-h-[110px]"
DARK_BINDING = (
    "darkMode ? 'border-accent-1 bg-primary-bg-dark text-text-main-dark' "
    ": 'border-accent-2 bg-primary-bg-light text-text-main-light'"
)


class StyledModelForm(forms.ModelForm):
    """Apply toto/Tailwind-friendly classes to ordinary Django form widgets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            widget.attrs.setdefault("x-bind:class", DARK_BINDING)

            if isinstance(widget, forms.CheckboxInput):
                widget.attrs.setdefault("class", "rounded border-current/30")
                widget.attrs.pop("x-bind:class", None)
            elif isinstance(widget, forms.Textarea):
                widget.attrs.setdefault("class", BASE_TEXTAREA_CLASS)
            elif isinstance(widget, forms.SelectMultiple):
                widget.attrs.setdefault("class", BASE_SELECT_CLASS + " min-h-[120px]")
            else:
                widget.attrs.setdefault("class", BASE_INPUT_CLASS)


class DateTimeLocalInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, attrs=None):
        base_attrs = {"class": BASE_INPUT_CLASS, "x-bind:class": DARK_BINDING}
        base_attrs.update(attrs or {})
        super().__init__(attrs=base_attrs, format="%Y-%m-%dT%H:%M")


class RobotModelForm(StyledModelForm):
    class Meta:
        model = RobotModel
        fields = [
            "manufacturer", "name", "slug", "kind", "description",
            "payload_capacity_kg", "max_speed_mps", "battery_capacity_wh",
            "nominal_runtime_minutes", "supported_capabilities", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "supported_capabilities": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class RobotCapabilityForm(StyledModelForm):
    class Meta:
        model = RobotCapability
        fields = ["name", "slug", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class RobotForm(StyledModelForm):
    class Meta:
        model = Robot
        fields = [
            "community", "model", "name", "callsign", "serial_number", "inventory_object",
            "status", "capabilities", "operator", "home_base", "current_location",
            "firmware_version", "hardware_revision", "battery_percent", "last_seen_at",
            "commissioned_at", "retired_at", "is_autonomous", "is_active", "metadata",
        ]
        widgets = {
            "last_seen_at": DateTimeLocalInput(),
            "commissioned_at": DateTimeLocalInput(),
            "retired_at": DateTimeLocalInput(),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class FleetForm(StyledModelForm):
    class Meta:
        model = Fleet
        fields = ["community", "name", "slug", "description", "manager", "is_active", "metadata"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class FleetMembershipForm(StyledModelForm):
    class Meta:
        model = FleetMembership
        fields = ["fleet", "robot", "role", "joined_at", "left_at"]
        widgets = {
            "joined_at": DateTimeLocalInput(),
            "left_at": DateTimeLocalInput(),
        }


class RobotMissionForm(StyledModelForm):
    class Meta:
        model = RobotMission
        fields = [
            "community", "fleet", "title", "description", "status", "requested_by", "supervisor",
            "origin", "destination", "route", "scheduled_start_at", "scheduled_end_at",
            "started_at", "completed_at", "priority", "payload", "constraints", "result",
            "detection", "kanban_task",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "scheduled_start_at": DateTimeLocalInput(),
            "scheduled_end_at": DateTimeLocalInput(),
            "started_at": DateTimeLocalInput(),
            "completed_at": DateTimeLocalInput(),
            "payload": forms.Textarea(attrs={"rows": 3}),
            "constraints": forms.Textarea(attrs={"rows": 3}),
            "result": forms.Textarea(attrs={"rows": 3}),
        }


class MissionWaypointForm(StyledModelForm):
    class Meta:
        model = MissionWaypoint
        fields = [
            "mission", "order", "label", "instruction", "address",
            "latitude", "longitude", "altitude_m", "hold_seconds", "metadata",
        ]
        widgets = {
            "instruction": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class RobotAssignmentForm(StyledModelForm):
    class Meta:
        model = RobotAssignment
        fields = [
            "mission", "robot", "status", "role", "accepted_at", "started_at",
            "completed_at", "failure_reason", "telemetry_summary",
        ]
        widgets = {
            "accepted_at": DateTimeLocalInput(),
            "started_at": DateTimeLocalInput(),
            "completed_at": DateTimeLocalInput(),
            "failure_reason": forms.Textarea(attrs={"rows": 3}),
            "telemetry_summary": forms.Textarea(attrs={"rows": 3}),
        }


class RobotTelemetryForm(StyledModelForm):
    class Meta:
        model = RobotTelemetry
        fields = [
            "robot", "mission", "recorded_at", "latitude", "longitude", "altitude_m",
            "speed_mps", "heading_degrees", "battery_percent", "battery_voltage",
            "temperature_c", "signal_strength", "cpu_percent", "memory_percent", "payload",
        ]
        widgets = {
            "recorded_at": DateTimeLocalInput(),
            "payload": forms.Textarea(attrs={"rows": 3}),
        }


class RobotEventForm(StyledModelForm):
    class Meta:
        model = RobotEvent
        fields = [
            "robot", "mission", "severity", "event_type", "message", "location",
            "data", "occurred_at", "acknowledged_by", "acknowledged_at",
        ]
        widgets = {
            "message": forms.Textarea(attrs={"rows": 3}),
            "data": forms.Textarea(attrs={"rows": 3}),
            "occurred_at": DateTimeLocalInput(),
            "acknowledged_at": DateTimeLocalInput(),
        }


class RobotCommandForm(StyledModelForm):
    class Meta:
        model = RobotCommand
        fields = ["robot", "mission", "command_type", "payload", "issued_by", "idempotency_key"]
        widgets = {"payload": forms.Textarea(attrs={"rows": 4})}


class ChargingDockForm(StyledModelForm):
    class Meta:
        model = ChargingDock
        fields = [
            "community", "name", "slug", "location", "supported_models",
            "power_kw", "slot_count", "is_active", "metadata",
        ]
        widgets = {"metadata": forms.Textarea(attrs={"rows": 3})}


class ChargingSessionForm(StyledModelForm):
    class Meta:
        model = ChargingSession
        fields = [
            "robot", "dock", "started_at", "ended_at", "start_battery_percent",
            "end_battery_percent", "energy_kwh", "metadata",
        ]
        widgets = {
            "started_at": DateTimeLocalInput(),
            "ended_at": DateTimeLocalInput(),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class RobotComponentForm(StyledModelForm):
    class Meta:
        model = RobotComponent
        fields = [
            "robot", "name", "component_type", "serial_number", "installed_at",
            "removed_at", "expected_lifetime_hours", "runtime_hours", "metadata",
        ]
        widgets = {
            "installed_at": DateTimeLocalInput(),
            "removed_at": DateTimeLocalInput(),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class MaintenanceTicketForm(StyledModelForm):
    class Meta:
        model = MaintenanceTicket
        fields = [
            "robot", "component", "title", "description", "status", "severity",
            "reported_by", "assigned_to", "opened_at", "due_at", "resolved_at",
            "closed_at", "cost_base_units", "cost_asset", "metadata",
        ]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
            "opened_at": DateTimeLocalInput(),
            "due_at": DateTimeLocalInput(),
            "resolved_at": DateTimeLocalInput(),
            "closed_at": DateTimeLocalInput(),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class MaintenanceActionForm(StyledModelForm):
    class Meta:
        model = MaintenanceAction
        fields = ["ticket", "performed_by", "action_type", "notes", "performed_at", "parts_used", "metadata"]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 4}),
            "performed_at": DateTimeLocalInput(),
            "parts_used": forms.Textarea(attrs={"rows": 3}),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class FirmwareReleaseForm(StyledModelForm):
    class Meta:
        model = FirmwareRelease
        fields = ["model", "version", "release_notes", "artifact_url", "checksum", "is_required", "released_at", "metadata"]
        widgets = {
            "release_notes": forms.Textarea(attrs={"rows": 4}),
            "released_at": DateTimeLocalInput(),
            "metadata": forms.Textarea(attrs={"rows": 3}),
        }


class FirmwareInstallationForm(StyledModelForm):
    class Meta:
        model = FirmwareInstallation
        fields = ["robot", "release", "installed_by", "started_at", "completed_at", "success", "log"]
        widgets = {
            "started_at": DateTimeLocalInput(),
            "completed_at": DateTimeLocalInput(),
            "log": forms.Textarea(attrs={"rows": 4}),
        }
