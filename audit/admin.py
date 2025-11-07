from django.contrib import admin
from django.urls import path, reverse
from django.utils.html import format_html
from django.conf import settings
from .models import AuditLog
import fnmatch
from pathlib import Path


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('appname', 'log_link', 'created_at')
    search_fields = ('appname', 'filepath')
    list_filter = ('created_at',)
    ordering = ('-created_at',)


    def log_link(self, obj):
        url = reverse('audit:view_log', args=[obj.appname])
        filename = Path(obj.filepath).name
        return format_html('<a href="{}" target="_blank">{}</a>', url, filename)