"""The settings row, and the runs — read-only.

`OcrRun` is registered without add or change permission on purpose: a run is a
record of work that happened and money that moved, and an admin who could edit
its counters could rewrite that record. Deleting one IS offered, because an
operator clearing an incident should not have to wait for the nightly sweep.
"""

from django.contrib import admin

from toto.ocr.models import OcrPage, OcrRun, OcrSettings


@admin.register(OcrSettings)
class OcrSettingsAdmin(admin.ModelAdmin):
    """One row, edited not created — the toto.weather singleton shape."""

    list_display = ("max_upload_mb", "max_pages_per_run", "retention_days",
                    "retention_enabled")
    fields = ("max_upload_mb", "max_pages_per_run", "retention_days",
              "retention_enabled")

    def has_add_permission(self, request):
        # There is exactly one settings row. Offering "add" would let an
        # operator create a second that nothing ever reads: `get()` is pinned
        # to pk=1, so the extra row would be edited with no effect at all.
        return not OcrSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Deleting it would not remove the limits, it would restore the
        # defaults on the next read — a confusing way to spell "set them back".
        return False


class OcrPageInline(admin.TabularInline):
    model = OcrPage
    extra = 0
    can_delete = False
    fields = ("number", "status", "attempts", "error")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(OcrRun)
class OcrRunAdmin(admin.ModelAdmin):
    list_display = ("id", "owner", "source_name", "status", "total_pages",
                    "pages_done", "pages_failed", "created_at")
    list_filter = ("status", "source_type")
    search_fields = ("source_name", "owner__username")
    readonly_fields = [f.name for f in OcrRun._meta.fields]
    inlines = [OcrPageInline]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
