from django.contrib import admin

from toto.notarius.models import ContractPdfJob, ContractTemplate


@admin.register(ContractTemplate)
class ContractTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "is_default", "updated_at")
    list_filter = ("is_default",)
    search_fields = ("name", "key", "description")
    prepopulated_fields = {"key": ("name",)}
    fields = ("name", "key", "description", "is_default", "latex_source")


@admin.register(ContractPdfJob)
class ContractPdfJobAdmin(admin.ModelAdmin):
    list_display = ("id", "vault_file", "status", "pdf_vault_file", "created_at", "finished_at")
    list_filter = ("status",)
    readonly_fields = ("celery_task_id", "created_at", "finished_at", "log")
