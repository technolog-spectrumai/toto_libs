from django.contrib import admin

from .models import FileServiceRun


@admin.register(FileServiceRun)
class FileServiceRunAdmin(admin.ModelAdmin):
    list_display = ("id", "service_key", "status", "owner", "bucket", "started_at", "finished_at")
    list_filter = ("service_key", "status")
    search_fields = ("service_key", "owner__username")
    readonly_fields = ("started_at", "finished_at", "task_id")
