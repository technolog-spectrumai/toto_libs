from django.contrib import admin

from toto.audit.models import AuditChain, AuditRecord


@admin.register(AuditChain)
class AuditChainAdmin(admin.ModelAdmin):
    list_display = ("key", "name", "created_at")
    readonly_fields = ("created_at",)


@admin.register(AuditRecord)
class AuditRecordAdmin(admin.ModelAdmin):
    """Read-only in the admin, because the model is read-only in the database.

    Without these three methods the admin renders Save and Delete buttons that
    raise ValidationError on click — a worse experience than not offering them.
    """

    list_display = ("sequence", "timestamp", "action", "app_label",
                    "object_description", "actor_username", "success")
    list_filter = ("app_label", "action", "success", "source")
    search_fields = ("object_description", "actor_username", "object_id", "correlation_id")
    date_hierarchy = "timestamp"
    ordering = ("-sequence",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
