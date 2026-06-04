from django.contrib import admin

from .models import (
    CommunityMagistrateSettings, Magistrate, MagistrateDecision,
    MagistrateFine, MagistrateReport, MagistrateRole,
)


@admin.register(MagistrateRole)
class MagistrateRoleAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug",
        "overseeing_tribunal", "overseeing_trade",
        "overseeing_merchandise", "overseeing_finance", "overseeing_public_order",
        "overseeing_education", "overseeing_relations",
        "overseeing_logistics", "overseeing_interior", "overseeing_productivity",
        "can_set_fines", "order",
    )
    list_editable = ("order",)
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name",)
    ordering = ("order", "name")


class MagistrateReportInline(admin.TabularInline):
    model = MagistrateReport
    extra = 0
    fields = ("title", "status", "reporting_period_start", "reporting_period_end", "submitted_at")
    readonly_fields = ("submitted_at",)


@admin.register(Magistrate)
class MagistrateAdmin(admin.ModelAdmin):
    list_display = ("person", "role", "community", "status", "term_start", "term_end", "elected_at")
    list_filter = ("status", "role", "community")
    search_fields = ("person__display_name", "role__name", "community__name")
    autocomplete_fields = ("role",)
    raw_id_fields = ("person", "community", "source_proposal")
    inlines = [MagistrateReportInline]
    ordering = ("-elected_at",)


@admin.register(MagistrateReport)
class MagistrateReportAdmin(admin.ModelAdmin):
    list_display = ("title", "magistrate", "status", "submitted_at", "acknowledged_by", "acknowledged_at")
    list_filter = ("status",)
    search_fields = ("title", "magistrate__person__display_name")
    raw_id_fields = ("magistrate", "acknowledged_by")
    ordering = ("-created_at",)


@admin.register(MagistrateDecision)
class MagistrateDecisionAdmin(admin.ModelAdmin):
    list_display = ("title", "decision_type", "magistrate", "community", "status", "created_at")
    list_filter = ("decision_type", "status", "community")
    search_fields = ("title", "magistrate__person__display_name", "body")
    raw_id_fields = ("magistrate", "community", "reviewed_by")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(CommunityMagistrateSettings)
class CommunityMagistrateSettingsAdmin(admin.ModelAdmin):
    list_display = ("community", "max_fine_pct", "fine_collection_account")
    raw_id_fields = ("community", "fine_collection_account")
    search_fields = ("community__name",)


@admin.register(MagistrateFine)
class MagistrateFineAdmin(admin.ModelAdmin):
    list_display = ("target_person", "fine_pct", "fine_amount_display", "asset", "status", "decision", "created_at")
    list_filter = ("status", "asset")
    search_fields = ("target_person__display_name", "infraction", "decision__title")
    raw_id_fields = ("decision", "target_person", "target_account", "asset", "obligation", "overturned_by")
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-created_at",)
