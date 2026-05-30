from django.contrib import admin
from django.utils.html import format_html

from .models import MediaJob, ProbeResult, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "slug", "owner", "bucket", "created_at"]
    search_fields = ["name", "slug"]
    raw_id_fields = ["owner", "bucket"]
    readonly_fields = ["slug", "created_at"]


def _requeue_failed(modeladmin, request, queryset):
    from .tasks_direct import run_direct_job
    eligible = queryset.filter(status__in=[MediaJob.Status.FAILED, MediaJob.Status.PENDING])
    count = 0
    for job in eligible:
        job.status = MediaJob.Status.PENDING
        job.error_message = ""
        job.save(update_fields=["status", "error_message"])
        run_direct_job.delay(job.id)
        count += 1
    modeladmin.message_user(request, f"Re-queued {count} job(s).")


_requeue_failed.short_description = "Re-queue selected failed/pending jobs"


@admin.register(MediaJob)
class MediaJobAdmin(admin.ModelAdmin):
    list_display = ["id", "task_name", "status", "progress_percent", "owner", "created_at", "duration_seconds"]
    list_filter  = ["task_name", "status"]
    search_fields = ["task_name", "error_message"]
    readonly_fields = [
        "task_name", "status", "progress_percent", "progress_message",
        "rendered_argv", "stdout", "stderr", "exit_code", "error_message",
        "output_metadata", "started_at", "finished_at", "created_at", "duration_seconds",
        "workflow_run", "workflow_node_run",
    ]
    actions = [_requeue_failed]
    raw_id_fields = ["input_file", "secondary_file", "output_file", "owner"]

    def has_add_permission(self, request):
        return False


@admin.register(ProbeResult)
class ProbeResultAdmin(admin.ModelAdmin):
    list_display = ["id", "vault_file", "duration_seconds", "width", "height", "video_codec", "audio_codec", "created_at"]
    readonly_fields = ["vault_file", "job", "raw", "format", "streams", "duration_seconds", "width", "height", "video_codec", "audio_codec", "created_at"]
    raw_id_fields = ["vault_file", "job"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
