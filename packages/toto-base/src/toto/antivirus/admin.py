from django.contrib import admin

from .models import ScanPreference, ScanResult


@admin.register(ScanResult)
class ScanResultAdmin(admin.ModelAdmin):
    """Read-only: a verdict is a record of what was concluded, not a setting."""

    list_display = ("file", "file_type", "verdict", "reason", "door", "scanned_at")
    list_filter = ("verdict", "file_type", "door")
    search_fields = ("file__title", "reason", "detail", "content_sha256")
    raw_id_fields = ("file", "scanned_by")
    readonly_fields = ("file", "content_sha256", "file_type", "verdict", "reason",
                       "detail", "line", "door", "scanned_by", "scanned_at")

    def has_add_permission(self, request):
        return False


@admin.register(ScanPreference)
class ScanPreferenceAdmin(admin.ModelAdmin):
    list_display = ("user", "types", "updated_at")
    raw_id_fields = ("user",)
