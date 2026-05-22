from django.contrib import admin

from .models import Magistrate, MagistrateReport, MagistrateRole


@admin.register(MagistrateRole)
class MagistrateRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "overseeing_mobilization", "overseeing_tribunal", "overseeing_trade", "overseeing_finance", "overseeing_public_order", "overseeing_legislation", "order")
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
