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


    def get_search_results(self, request, queryset, search_term):
        # Default search behavior
        base_qs, use_distinct = super().get_search_results(request, queryset, search_term)

        # Custom fnmatch search if search_term starts with "text:"
        if search_term.startswith("text:"):
            pattern = search_term[5:].strip().lower()
            matched_ids = []

            log_dir = Path(settings.BASE_DIR) / 'logs'
            for log in queryset:
                log_path = Path(log.filepath)
                if not log_path.exists():
                    continue
                try:
                    with open(log_path, 'r') as f:
                        for line in f:
                            if fnmatch.fnmatch(line.lower(), f"*{pattern}*"):
                                matched_ids.append(log.id)
                                break  # Only need one match per file
                except Exception as e:
                    self.message_user(request, f"Error reading {log_path.name}: {e}", level='warning')

            base_qs = queryset.filter(id__in=matched_ids)

        return base_qs, use_distinct
