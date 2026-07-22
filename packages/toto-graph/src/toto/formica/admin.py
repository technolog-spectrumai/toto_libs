from django.contrib import admin

from .models import CasteConfig, Colony, ColonyCycle, ForagerReport, FormicaProposal


class CasteConfigInline(admin.TabularInline):
    model = CasteConfig
    extra = 0


@admin.register(Colony)
class ColonyAdmin(admin.ModelAdmin):
    list_display = (
        "name", "enabled", "paused", "trusted", "interval_minutes", "last_run_at",
    )
    list_filter = ("enabled", "paused", "trusted")
    search_fields = ("name", "slug")
    readonly_fields = ("slug", "last_run_at", "created_at", "updated_at")
    inlines = [CasteConfigInline]


@admin.register(ColonyCycle)
class ColonyCycleAdmin(admin.ModelAdmin):
    """Read-only audit browsing — cycles are created by the engine."""

    list_display = (
        "id", "colony", "number", "status", "triggered_by", "started_at", "finished_at",
    )
    list_filter = ("status", "triggered_by")
    readonly_fields = [f.name for f in ColonyCycle._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(FormicaProposal)
class FormicaProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "colony", "cycle", "status", "total_ops", "created_at")
    list_filter = ("status", "created_at")
    readonly_fields = ("ops", "summary", "apply_result", "created_at", "updated_at")


@admin.register(ForagerReport)
class ForagerReportAdmin(admin.ModelAdmin):
    list_display = ("id", "cycle", "created_at")
    readonly_fields = [f.name for f in ForagerReport._meta.fields]

    def has_add_permission(self, request):
        return False
