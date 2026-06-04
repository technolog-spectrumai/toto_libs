from django.contrib import admin
from .models import (
    AchievementBadge,
    PersonAchievement,
    IncidentType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationReportEvidence,
    MobilizationEvent,
    EmergencyStatus,
    EmergencyEquipmentAccess,
)


@admin.register(AchievementBadge)
class AchievementBadgeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "category", "icon", "order")
    list_filter = ("category",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    ordering = ("category", "order", "name")


@admin.register(PersonAchievement)
class PersonAchievementAdmin(admin.ModelAdmin):
    list_display = ("person", "badge", "deployment", "awarded_by", "awarded_at")
    list_filter = ("badge__category",)
    search_fields = ("person__display_name", "badge__name")
    readonly_fields = ("awarded_at",)
    autocomplete_fields = ("badge",)


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
    fields = ("incident", "evidence_role", "weight", "note", "added_by")
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
    list_display = ("report", "incident", "evidence_role", "weight", "added_by", "added_at")
    list_filter = ("evidence_role", "weight")
    search_fields = ("report__title", "incident__title")
    readonly_fields = ("added_at",)


class DeploymentInline(admin.TabularInline):
    from toto.response.models import Deployment as _Deployment
    model = _Deployment
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


class EmergencyEquipmentAccessInline(admin.TabularInline):
    model = EmergencyEquipmentAccess
    extra = 0
    fields = ("item", "deployment", "is_hybrid", "quantity", "authorized_by", "returned_at")
    readonly_fields = ("authorized_at",)


@admin.register(EmergencyStatus)
class EmergencyStatusAdmin(admin.ModelAdmin):
    list_display = ("event", "community", "zone", "level", "status", "declared_at", "lifted_at", "is_active")
    list_filter = ("level", "status", "allows_inventory_access")
    search_fields = ("event__title", "community__name")
    readonly_fields = ("created_at", "updated_at")
    inlines = [EmergencyEquipmentAccessInline]


@admin.register(EmergencyEquipmentAccess)
class EmergencyEquipmentAccessAdmin(admin.ModelAdmin):
    list_display = ("item", "emergency", "deployment", "is_hybrid", "quantity", "authorized_by", "returned_at")
    list_filter = ("is_hybrid",)
    search_fields = ("item__name",)
    readonly_fields = ("authorized_at",)
