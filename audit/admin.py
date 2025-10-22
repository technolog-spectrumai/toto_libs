# auditlog/admin.py

from django.contrib import admin
from django.utils.html import format_html
from django.conf import settings
from .models import AuditLog
from pathlib import Path

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('appname', 'log_link', 'created_at')
    search_fields = ('appname', 'filepath')
    list_filter = ('created_at',)
    ordering = ('-created_at',)

    def log_link(self, obj):
        # Get the full path from LOGGING settings
        app_logger = settings.LOGGING['handlers'].get(f"{obj.appname}_file")
        if not app_logger:
            return "No logger configured"

        full_path = Path(app_logger['filename'])
        filename = full_path.name

        # Assuming logs are served via /media/logs/
        return format_html(
            '<a href="{}" target="_blank">{}</a>',
            full_path,
            full_path
        )

    log_link.short_description = 'Log File'
