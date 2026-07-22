from django.contrib import admin

from .models import Snapshot


@admin.register(Snapshot)
class SnapshotAdmin(admin.ModelAdmin):
    """Read-only browsing of the snapshot history (retention is monit_prune's job)."""

    date_hierarchy = "created"
    list_display = ("created", "sys_cpu_percent", "db_ok", "db_latency_ms",
                    "redis_ok", "celery_ok", "web_ok", "web_latency_ms")
    list_filter = ("db_ok", "redis_ok", "celery_ok", "web_ok")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
