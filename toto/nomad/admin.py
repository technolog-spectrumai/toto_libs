from django.contrib import admin, messages

from . import service
from .models import OnionIdentity


@admin.register(OnionIdentity)
class OnionIdentityAdmin(admin.ModelAdmin):
    list_display = ("service_id", "is_active", "created", "retired_at", "migrated_by")
    list_filter = ("is_active",)
    search_fields = ("service_id",)
    readonly_fields = ("service_id", "is_active", "created", "retired_at", "migrated_by")
    actions = ["migrate_onion"]

    def has_add_permission(self, request):
        # Identities are minted by nomad, never hand-added.
        return False

    @admin.action(description="Migrate onion (mint a new .onion, retire the current one)")
    def migrate_onion(self, request, queryset):
        # A server-wide rotation — the selected rows are irrelevant; gate on superuser.
        if not request.user.is_superuser:
            self.message_user(request, "Only superusers can migrate the onion.", messages.ERROR)
            return
        try:
            new_onion = service.migrate_onion(triggered_by=request.user)
            self.message_user(request, f"Onion migrated. New address: {new_onion}.onion", messages.SUCCESS)
        except Exception as exc:  # noqa: BLE001
            self.message_user(request, f"Onion migration failed: {exc}", messages.ERROR)
