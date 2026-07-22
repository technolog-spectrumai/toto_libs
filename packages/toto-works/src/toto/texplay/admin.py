from django.contrib import admin

from .models import TexPlayJob


@admin.register(TexPlayJob)
class TexPlayJobAdmin(admin.ModelAdmin):
    list_display = ("pk", "vault_file", "status", "created_at", "finished_at")
    list_filter = ("status",)
    raw_id_fields = ("vault_file", "pdf_vault_file")
    readonly_fields = ("celery_task_id", "created_at", "finished_at")
