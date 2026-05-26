from django.contrib import admin

try:
    from toto.core.base_admin import TotoModelAdmin
except Exception:  # pragma: no cover - makes app usable outside toto during extraction/tests
    TotoModelAdmin = admin.ModelAdmin

from .models import (
    ChargingDock,
    ChargingSession,
    ComponentReading,
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


class FleetMembershipInline(admin.TabularInline):
    model = FleetMembership
    extra = 0
    autocomplete_fields = ("robot",)
    fields = ("robot", "role", "joined_at", "left_at")


class RobotAssignmentInline(admin.TabularInline):
    model = RobotAssignment
    extra = 0
    autocomplete_fields = ("robot",)
    fields = ("robot", "role", "status", "accepted_at", "started_at", "completed_at")


class MissionWaypointInline(admin.TabularInline):
    model = MissionWaypoint
    extra = 0
    autocomplete_fields = ("address",)
    fields = ("order", "label", "address", "latitude", "longitude", "altitude_m", "hold_seconds")
    ordering = ("order",)


class RobotComponentInline(admin.TabularInline):
    model = RobotComponent
    extra = 0
    fields = ("name", "component_type", "serial_number", "installed_at", "removed_at", "runtime_hours")


class MaintenanceActionInline(admin.TabularInline):
    model = MaintenanceAction
    extra = 0
    autocomplete_fields = ("performed_by",)
    fields = ("action_type", "performed_by", "performed_at", "notes")


@admin.register(RobotModel)
class RobotModelAdmin(TotoModelAdmin):
    list_display = ("id", "manufacturer", "name", "kind", "battery_capacity_wh", "nominal_runtime_minutes")
    list_display_links = ("id", "name")
    list_filter = ("kind", "manufacturer")
    search_fields = ("manufacturer", "name", "slug", "description")
    prepopulated_fields = {"slug": ("manufacturer", "name")}


@admin.register(RobotCapability)
class RobotCapabilityAdmin(TotoModelAdmin):
    list_display = ("id", "name", "slug")
    list_display_links = ("id", "name")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Robot)
class RobotAdmin(TotoModelAdmin):
    list_display = (
        "id", "callsign", "name", "community", "model", "status",
        "battery_percent", "operator", "current_location", "last_seen_at", "is_active",
    )
    list_display_links = ("id", "callsign", "name")
    list_filter = ("status", "community", "model__kind", "is_autonomous", "is_active")
    search_fields = ("name", "callsign", "serial_number", "model__name", "model__manufacturer")
    autocomplete_fields = ("community", "model", "operator", "home_base", "current_location", "inventory_object")
    filter_horizontal = ("capabilities",)
    inlines = (RobotComponentInline,)
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Fleet)
class FleetAdmin(TotoModelAdmin):
    list_display = ("id", "name", "community", "manager", "is_active", "robot_count")
    list_display_links = ("id", "name")
    list_filter = ("community", "is_active")
    search_fields = ("name", "slug", "description", "manager__display_name")
    autocomplete_fields = ("community", "manager")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (FleetMembershipInline,)
    readonly_fields = ("uuid", "created_at", "updated_at")

    def robot_count(self, obj):
        return obj.robots.count()


@admin.register(FleetMembership)
class FleetMembershipAdmin(TotoModelAdmin):
    list_display = ("id", "fleet", "robot", "role", "joined_at", "left_at")
    list_filter = ("fleet", "role")
    search_fields = ("fleet__name", "robot__name", "robot__callsign", "role")
    autocomplete_fields = ("fleet", "robot")


@admin.register(RobotMission)
class RobotMissionAdmin(TotoModelAdmin):
    list_display = ("id", "title", "community", "fleet", "status", "priority", "scheduled_start_at", "started_at", "completed_at")
    list_display_links = ("id", "title")
    list_filter = ("status", "community", "fleet", "priority")
    search_fields = ("title", "description", "fleet__name")
    autocomplete_fields = (
        "community", "fleet", "requested_by", "supervisor", "origin", "destination",
        "route", "detection", "kanban_task",
    )
    inlines = (MissionWaypointInline, RobotAssignmentInline)
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(MissionWaypoint)
class MissionWaypointAdmin(TotoModelAdmin):
    list_display = ("id", "mission", "order", "label", "address", "latitude", "longitude")
    list_filter = ("mission__status",)
    search_fields = ("mission__title", "label", "instruction")
    autocomplete_fields = ("mission", "address")


@admin.register(RobotAssignment)
class RobotAssignmentAdmin(TotoModelAdmin):
    list_display = ("id", "mission", "robot", "role", "status", "started_at", "completed_at")
    list_filter = ("status", "role")
    search_fields = ("mission__title", "robot__name", "robot__callsign", "role")
    autocomplete_fields = ("mission", "robot")


@admin.register(RobotTelemetry)
class RobotTelemetryAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "mission", "recorded_at", "battery_percent", "speed_mps", "latitude", "longitude")
    list_filter = ("robot__community", "robot", "mission")
    search_fields = ("robot__name", "robot__callsign", "mission__title")
    autocomplete_fields = ("robot", "mission")
    readonly_fields = ("recorded_at",)


@admin.register(RobotEvent)
class RobotEventAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "mission", "severity", "event_type", "occurred_at", "acknowledged_at")
    list_filter = ("severity", "event_type", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "mission__title", "message", "event_type")
    autocomplete_fields = ("robot", "mission", "location", "acknowledged_by")


@admin.register(ChargingDock)
class ChargingDockAdmin(TotoModelAdmin):
    list_display = ("id", "name", "community", "location", "power_kw", "slot_count", "is_active")
    list_filter = ("community", "is_active")
    search_fields = ("name", "slug", "location__locality_name")
    autocomplete_fields = ("community", "location")
    filter_horizontal = ("supported_models",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ChargingSession)
class ChargingSessionAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "dock", "started_at", "ended_at", "start_battery_percent", "end_battery_percent", "energy_kwh")
    list_filter = ("dock", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "dock__name")
    autocomplete_fields = ("robot", "dock")


@admin.register(RobotComponent)
class RobotComponentAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "name", "component_type", "serial_number", "installed_at", "removed_at", "runtime_hours")
    list_filter = ("component_type", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "name", "serial_number")
    autocomplete_fields = ("robot",)


@admin.register(ComponentReading)
class ComponentReadingAdmin(TotoModelAdmin):
    list_display = ("id", "component", "metric", "value", "unit", "recorded_at")
    list_filter = ("metric", "component__component_type")
    search_fields = ("component__name", "component__robot__callsign", "metric", "unit")
    autocomplete_fields = ("component",)


@admin.register(MaintenanceTicket)
class MaintenanceTicketAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "title", "status", "severity", "assigned_to", "opened_at", "due_at", "resolved_at")
    list_filter = ("status", "severity", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "title", "description")
    autocomplete_fields = ("robot", "component", "reported_by", "assigned_to", "cost_asset")
    inlines = (MaintenanceActionInline,)
    readonly_fields = ("uuid",)


@admin.register(MaintenanceAction)
class MaintenanceActionAdmin(TotoModelAdmin):
    list_display = ("id", "ticket", "action_type", "performed_by", "performed_at")
    list_filter = ("action_type",)
    search_fields = ("ticket__title", "action_type", "notes")
    autocomplete_fields = ("ticket", "performed_by")


@admin.register(FirmwareRelease)
class FirmwareReleaseAdmin(TotoModelAdmin):
    list_display = ("id", "model", "version", "is_required", "released_at")
    list_filter = ("model", "is_required")
    search_fields = ("model__name", "version", "release_notes", "checksum")
    autocomplete_fields = ("model",)


@admin.register(FirmwareInstallation)
class FirmwareInstallationAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "release", "installed_by", "started_at", "completed_at", "success")
    list_filter = ("success", "release__model", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "release__version", "log")
    autocomplete_fields = ("robot", "release", "installed_by")


@admin.register(RobotCommand)
class RobotCommandAdmin(TotoModelAdmin):
    list_display = ("id", "robot", "mission", "command_type", "issued_by", "issued_at", "sent_at", "acknowledged_at", "completed_at", "success")
    list_filter = ("command_type", "success", "robot__community")
    search_fields = ("robot__name", "robot__callsign", "mission__title", "command_type", "idempotency_key")
    autocomplete_fields = ("robot", "mission", "issued_by")
    readonly_fields = ("issued_at",)
