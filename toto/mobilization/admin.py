from django.contrib import admin
from .models import (
    IncidentType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationReportEvidence,
    MobilizationEvent,
    Deployment,
    DeploymentAssignment,
    DeploymentEquipment,
    DeploymentRoute,
    EvacuationRoute,
    Intervention,
    EmergencyStatus,
    EmergencyEquipmentAccess,
)


@admin.register(IncidentType)
class IncidentTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "order")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)


class ResponderSkillInline(admin.TabularInline):
    model = ResponderSkill
    extra = 0
    fields = ("skill", "level", "verified_by", "verified_at")
    autocomplete_fields = ("skill",)


@admin.register(Responder)
class ResponderAdmin(admin.ModelAdmin):
    list_display = ("person", "current_status", "is_active", "is_trained", "is_background_checked", "last_status_changed_at")
    list_filter = ("current_status", "is_active", "is_trained", "is_background_checked")
    search_fields = ("person__display_name",)
    filter_horizontal = ("communities",)
    readonly_fields = ("created_at", "updated_at", "last_status_changed_at")
    inlines = [ResponderSkillInline]


@admin.register(ResponderSkill)
class ResponderSkillAdmin(admin.ModelAdmin):
    list_display = ("responder", "skill", "level", "verified_by", "verified_at")
    list_filter = ("level",)
    search_fields = ("responder__person__display_name", "skill__title")


class MobilizationReportEvidenceInline(admin.TabularInline):
    model = MobilizationReportEvidence
    extra = 0
    fields = ("detection", "evidence_role", "weight", "note", "added_by")
    readonly_fields = ("added_at",)


@admin.register(MobilizationReport)
class MobilizationReportAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "incident_type", "status", "severity", "submitted_by", "enacted_at")
    list_filter = ("status", "severity", "incident_type")
    search_fields = ("title", "summary")
    readonly_fields = ("created_at", "updated_at", "enacted_at", "rejected_at", "closed_at")
    inlines = [MobilizationReportEvidenceInline]


@admin.register(MobilizationReportEvidence)
class MobilizationReportEvidenceAdmin(admin.ModelAdmin):
    list_display = ("report", "detection", "evidence_role", "weight", "added_by", "added_at")
    list_filter = ("evidence_role", "weight")
    search_fields = ("report__title", "detection__title")
    readonly_fields = ("added_at",)


class DeploymentInline(admin.TabularInline):
    model = Deployment
    extra = 0
    fields = ("title", "deployment_type", "priority", "status", "coordinator")
    show_change_link = True


@admin.register(MobilizationEvent)
class MobilizationEventAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "incident_type", "status", "coordinator", "started_at", "ended_at")
    list_filter = ("status", "incident_type")
    search_fields = ("title", "description")
    readonly_fields = ("created_at", "updated_at")
    inlines = [DeploymentInline]


class DeploymentAssignmentInline(admin.TabularInline):
    model = DeploymentAssignment
    extra = 0
    fields = ("responder", "role", "status", "assigned_by", "confirmed_at", "released_at")
    readonly_fields = ("confirmed_at", "released_at")


class InterventionInline(admin.TabularInline):
    model = Intervention
    extra = 0
    fields = ("title", "intervention_type", "priority", "status", "assigned_to", "is_required")
    show_change_link = True


@admin.register(Deployment)
class DeploymentAdmin(admin.ModelAdmin):
    list_display = ("title", "event", "community", "deployment_type", "priority", "status", "coordinator")
    list_filter = ("status", "priority", "deployment_type")
    search_fields = ("title", "objective")
    readonly_fields = ("created_at", "updated_at")
    inlines = [DeploymentAssignmentInline, InterventionInline]


@admin.register(DeploymentAssignment)
class DeploymentAssignmentAdmin(admin.ModelAdmin):
    list_display = ("responder", "deployment", "role", "status", "assigned_by", "confirmed_at")
    list_filter = ("status", "role")
    search_fields = ("responder__person__display_name", "deployment__title")
    readonly_fields = ("confirmed_at", "released_at")


@admin.register(Intervention)
class InterventionAdmin(admin.ModelAdmin):
    list_display = ("title", "deployment", "intervention_type", "priority", "status", "assigned_to", "is_required", "completed_at")
    list_filter = ("status", "priority", "intervention_type", "is_required")
    search_fields = ("title", "description")
    readonly_fields = ("created_at", "updated_at", "started_at", "completed_at")


@admin.register(EvacuationRoute)
class EvacuationRouteAdmin(admin.ModelAdmin):
    list_display = ("name", "event", "route_type", "status")
    list_filter = ("route_type", "status")
    search_fields = ("name",)


@admin.register(DeploymentRoute)
class DeploymentRouteAdmin(admin.ModelAdmin):
    list_display = ("deployment", "route", "route_type")
    list_filter = ("route_type",)


@admin.register(DeploymentEquipment)
class DeploymentEquipmentAdmin(admin.ModelAdmin):
    list_display = ("item", "deployment", "quantity", "allocated_at")
    search_fields = ("item__name", "deployment__title")


class EmergencyEquipmentAccessInline(admin.TabularInline):
    model = EmergencyEquipmentAccess
    extra = 0
    fields = ("item", "deployment", "is_hybrid", "quantity", "authorized_by", "returned_at")
    readonly_fields = ("authorized_at",)


@admin.register(EmergencyStatus)
class EmergencyStatusAdmin(admin.ModelAdmin):
    list_display = ("event", "community", "zone", "level", "status", "declared_at", "lifted_at", "is_active")
    list_filter = ("level", "status", "allows_asset_requisition", "allows_inventory_access")
    search_fields = ("event__title", "community__name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [EmergencyEquipmentAccessInline]


@admin.register(EmergencyEquipmentAccess)
class EmergencyEquipmentAccessAdmin(admin.ModelAdmin):
    list_display = ("item", "emergency", "deployment", "is_hybrid", "quantity", "authorized_by", "returned_at")
    list_filter = ("is_hybrid",)
    search_fields = ("item__name",)
    readonly_fields = ("authorized_at",)
