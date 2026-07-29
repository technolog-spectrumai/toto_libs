from django.contrib import admin

from .models import FileJob


@admin.register(FileJob)
class FileJobAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "command", "status", "owner", "created_at"]
    list_filter = ["command", "status"]
    search_fields = ["name", "command", "celery_task_id"]
    readonly_fields = ["command", "owner", "celery_task_id", "status",
                       "inputs", "params", "output", "created_at"]

    def has_add_permission(self, request):
        return False
