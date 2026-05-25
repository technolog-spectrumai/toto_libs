from django.contrib import admin
from .models import (
    InterventionType,
    Deployment,
    DeploymentAssignment,
    DeploymentEquipment,
    DeploymentRoute,
    EvacuationRoute,
    Intervention,
)


@admin.register(InterventionType)
class InterventionTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "icon", "order")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    ordering = ("order", "name")


class DeploymentAssignmentInline(admin.TabularInline):
    model = DeploymentAssignment
    extra = 0
    fields = ("responder", "role", "status", "assigned_by", "confirmed_at", "released_at")
    readonly_fields = ("confirmed_at", "released_at")


class InterventionInline(admin.TabularInline):
    model = Intervention
    extra = 0
    fields = ("title", "intervention_type", "priority", "status", "assigned_to", "is_required")
    autocomplete_fields = ("intervention_type",)
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
    list_filter = ("status", "priority", "intervention_type__name", "is_required")
    search_fields = ("title", "description")
    autocomplete_fields = ("intervention_type",)
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
