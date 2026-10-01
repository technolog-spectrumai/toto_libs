from django.contrib import admin

from .models import BeatEntry, CheckState, Snapshot, TaskRun


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


@admin.register(CheckState)
class CheckStateAdmin(admin.ModelAdmin):
    """Read-only: the scheduled run (toto.monit.alerts) is the only writer."""

    list_display = ("key", "status", "since", "checked_at", "alerted_status",
                    "alerted_at")
    list_filter = ("status",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TaskRun)
class TaskRunAdmin(admin.ModelAdmin):
    """Read-only: Celery's signals (toto.monit.heartbeats) are the only writer,
    monit_prune the only one that deletes."""

    date_hierarchy = "started_at"
    list_display = ("task", "status", "started_at", "finished_at", "summary")
    list_filter = ("status", "task")
    search_fields = ("task", "task_id")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BeatEntry)
class BeatEntryAdmin(admin.ModelAdmin):
    """Read-only: written when celery beat starts (toto.monit.heartbeats)."""

    list_display = ("name", "task", "first_seen", "last_seen")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
