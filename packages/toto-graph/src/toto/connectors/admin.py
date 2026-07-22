from django.contrib import admin

from .models import ConnectorRun, DataConnector


@admin.register(DataConnector)
class DataConnectorAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "http_connector",
        "extractor_kind",
        "trusted",
        "is_active",
        "schedule_enabled",
        "next_run_at",
        "last_run_at",
    )
    list_filter = ("trusted", "is_active", "schedule_enabled", "extractor_kind")
    search_fields = ("name", "slug", "description")
    readonly_fields = ("slug", "next_run_at", "last_run_at", "created_at", "updated_at")


@admin.register(ConnectorRun)
class ConnectorRunAdmin(admin.ModelAdmin):
    """Read-only audit browsing — runs are created by the runner, never by hand."""

    list_display = (
        "id",
        "connector",
        "status",
        "triggered",
        "created_by",
        "created_at",
        "finished_at",
        "proposal",
    )
    list_filter = ("status", "triggered", "created_at")
    search_fields = ("connector__name", "connector__slug", "error")
    readonly_fields = [f.name for f in ConnectorRun._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
