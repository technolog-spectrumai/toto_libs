from django.contrib import admin, messages
from django.http import HttpResponseRedirect

from .models import SyncRule, SyncRun, SyncObjectRun


try:
    from toto.core.base_admin import TotoModelAdmin
except Exception:
    TotoModelAdmin = admin.ModelAdmin


@admin.register(SyncRule)
class SyncRuleAdmin(TotoModelAdmin):
    list_display = (
        "name",
        "platform",
        "model_label",
        "direction",
        "enabled",
        "sync_creates",
        "sync_updates",
        "sync_deletes",
        "last_pushed_at",
        "last_pulled_at",
    )

    list_filter = (
        "enabled",
        "direction",
        "sync_creates",
        "sync_updates",
        "sync_deletes",
        "platform",
    )

    search_fields = (
        "name",
        "model_label",
        "platform__site_name",
        "platform__domain",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_pushed_at",
        "last_pulled_at",
    )

    fieldsets = (
        (None, {
            "fields": (
                "platform",
                "name",
                "enabled",
                "model_label",
                "direction",
            )
        }),
        ("Selection", {
            "fields": (
                "fields",
                "filters",
                "include_dependencies",
            )
        }),
        ("Behavior", {
            "fields": (
                "sync_creates",
                "sync_updates",
                "sync_deletes",
                "conflict_policy",
            )
        }),
        ("State", {
            "fields": (
                "last_pushed_at",
                "last_pulled_at",
                "created_at",
                "updated_at",
            )
        }),
    )

    actions = (
        "run_selected_rules",
    )

    def run_selected_rules(self, request, queryset):
        """
        Optional action.

        This intentionally imports SyncRunner lazily so this models/admin-only app
        can be installed before service code exists. If you have not added a
        noosphere.services.runner.SyncRunner yet, this action will show an error
        instead of breaking admin import.
        """
        try:
            from .services.runner import SyncRunner
        except Exception as exc:
            self.message_user(
                request,
                f"Sync services are not installed yet: {exc}",
                level=messages.ERROR,
            )
            return HttpResponseRedirect(request.get_full_path())

        ran = 0
        failed = 0

        for rule in queryset.filter(enabled=True):
            try:
                SyncRunner(platform=rule.platform).run_rule(rule)
                ran += 1
            except Exception as exc:
                failed += 1
                self.message_user(
                    request,
                    f"{rule}: failed: {exc}",
                    level=messages.ERROR,
                )

        if ran:
            self.message_user(
                request,
                f"Ran {ran} sync rule(s).",
                level=messages.SUCCESS,
            )

        if failed and not ran:
            self.message_user(
                request,
                "No sync rules completed successfully.",
                level=messages.ERROR,
            )

        return HttpResponseRedirect(request.get_full_path())

    run_selected_rules.short_description = "Run selected sync rules"


class SyncObjectRunInline(admin.TabularInline):
    model = SyncObjectRun
    extra = 0
    can_delete = False

    fields = (
        "model_label",
        "uid",
        "action",
        "status",
        "message",
        "created_at",
    )

    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SyncRun)
class SyncRunAdmin(TotoModelAdmin):
    list_display = (
        "platform",
        "rule",
        "direction",
        "status",
        "started_at",
        "finished_at",
        "exported_count",
        "imported_count",
        "created_count",
        "updated_count",
        "skipped_count",
        "deleted_count",
        "failed_count",
    )

    list_filter = (
        "status",
        "direction",
        "platform",
        "started_at",
    )

    search_fields = (
        "rule__name",
        "rule__model_label",
        "platform__site_name",
        "platform__domain",
        "message",
        "package_hash",
    )

    readonly_fields = (
        "platform",
        "rule",
        "direction",
        "status",
        "started_at",
        "finished_at",
        "exported_count",
        "imported_count",
        "created_count",
        "updated_count",
        "skipped_count",
        "deleted_count",
        "failed_count",
        "package_hash",
        "remote_status_code",
        "remote_response",
        "message",
    )

    fieldsets = (
        (None, {
            "fields": (
                "platform",
                "rule",
                "direction",
                "status",
            )
        }),
        ("Timing", {
            "fields": (
                "started_at",
                "finished_at",
            )
        }),
        ("Counts", {
            "fields": (
                "exported_count",
                "imported_count",
                "created_count",
                "updated_count",
                "skipped_count",
                "deleted_count",
                "failed_count",
            )
        }),
        ("Remote response", {
            "fields": (
                "remote_status_code",
                "remote_response",
            )
        }),
        ("Package", {
            "fields": (
                "package_hash",
            )
        }),
        ("Message", {
            "fields": (
                "message",
            )
        }),
    )

    inlines = (
        SyncObjectRunInline,
    )

    def has_add_permission(self, request):
        return False


@admin.register(SyncObjectRun)
class SyncObjectRunAdmin(TotoModelAdmin):
    list_display = (
        "run",
        "model_label",
        "uid",
        "action",
        "status",
        "created_at",
    )

    list_filter = (
        "action",
        "status",
        "model_label",
        "created_at",
    )

    search_fields = (
        "uid",
        "model_label",
        "message",
        "run__rule__name",
        "run__rule__model_label",
    )

    readonly_fields = (
        "run",
        "model_label",
        "uid",
        "action",
        "status",
        "message",
        "payload",
        "created_at",
    )

    fieldsets = (
        (None, {
            "fields": (
                "run",
                "model_label",
                "uid",
                "action",
                "status",
            )
        }),
        ("Details", {
            "fields": (
                "message",
                "payload",
                "created_at",
            )
        }),
    )

    def has_add_permission(self, request):
        return False
