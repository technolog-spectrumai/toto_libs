from django.contrib import admin

from .models import AmbrosiaSettings, Workspace


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "kind", "execution", "bucket",
                    "last_opened_at", "created_at")
    list_filter = ("kind", "execution", "created_at")
    search_fields = ("name", "slug", "owner__username", "bucket__name")
    raw_id_fields = ("owner", "bucket", "root_directory")
    readonly_fields = ("slug", "created_at", "updated_at", "last_opened_at")
    ordering = ("-created_at",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner", "bucket")


@admin.register(AmbrosiaSettings)
class AmbrosiaSettingsAdmin(admin.ModelAdmin):
    """One row, edited not created — the toto.weather singleton shape."""

    list_display = ("max_workspaces_per_user",)
    fields = ("max_workspaces_per_user",)

    def has_add_permission(self, request):
        # There is exactly one settings row. Offering "add" would let an
        # operator create a second that nothing ever reads: `get()` is pinned
        # to pk=1, so the extra row would be edited with no effect at all.
        return not AmbrosiaSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        # Deleting it would not remove the limit, it would restore the default
        # on the next read — a confusing way to spell "set it to 0".
        return False
