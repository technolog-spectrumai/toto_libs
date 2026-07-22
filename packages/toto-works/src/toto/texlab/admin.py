from django.contrib import admin

from .models import CompileRun


@admin.register(CompileRun)
class CompileRunAdmin(admin.ModelAdmin):
    list_display = ("pk", "vault_file", "status", "started_at", "finished_at")
    list_filter = ("status",)
    raw_id_fields = ("vault_file", "workflow_run")
    readonly_fields = ("task_id", "started_at", "finished_at")
