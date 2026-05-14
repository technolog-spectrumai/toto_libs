from django.contrib import admin, messages
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import path, reverse
from django.utils.html import format_html


try:
    from toto.core.base_admin import TotoModelAdmin
except Exception:
    TotoModelAdmin = admin.ModelAdmin


from .forms import RemotePlatformAdminForm, RemotePlatformSyncConsoleForm
from .models import RemotePlatform, SyncRule, SyncRun, SyncObjectRun
from .registry import get_sync_adapter


@admin.register(RemotePlatform)
class RemotePlatformAdmin(TotoModelAdmin):
    form = RemotePlatformAdminForm

    list_display = (
        "name",
        "local_platform",
        "base_url",
        "enabled",
        "uplink_backend",
        "downlink_backend",
        "timeout_seconds",
        "last_seen_at",
        "sync_console_link",
    )

    list_filter = (
        "enabled",
        "local_platform",
        "uplink_backend",
        "downlink_backend",
    )

    search_fields = (
        "name",
        "base_url",
        "local_platform__site_name",
        "local_platform__domain",
    )

    readonly_fields = (
        "last_seen_at",
        "created_at",
        "updated_at",
        "sync_console_link",
    )

    fieldsets = (
        (None, {
            "fields": (
                "local_platform",
                "name",
                "enabled",
                "base_url",
            )
        }),
        ("Authentication", {
            "fields": (
                "outgoing_secret_key",
                "incoming_secret_key",
            )
        }),
        ("Transport", {
            "fields": (
                "uplink_backend",
                "downlink_backend",
                "timeout_seconds",
            )
        }),
        ("State", {
            "fields": (
                "last_seen_at",
                "created_at",
                "updated_at",
            )
        }),
        ("Sync", {
            "fields": (
                "sync_console_link",
            )
        }),
        ("Notes", {
            "fields": (
                "notes",
            )
        }),
    )

    actions = (
        "sync_console_action",
        "auto_populate_rules",
    )

    def get_urls(self):
        return [
            path(
                "sync-console/<int:remote_platform_id>/",
                self.admin_site.admin_view(self.sync_console_view),
                name="noosphere_remoteplatform_sync_console",
            ),
        ] + super().get_urls()

    def sync_console_link(self, obj):
        if not obj or not obj.pk:
            return "-"

        url = reverse(
            "admin:noosphere_remoteplatform_sync_console",
            args=[obj.pk],
        )

        return format_html(
            '<a class="button" href="{}">Open sync console</a>',
            url,
        )

    sync_console_link.short_description = "Sync console"

    def sync_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Select exactly one remote platform.",
                level=messages.ERROR,
            )
            return

        return redirect(f"sync-console/{queryset.first().id}/")

    sync_console_action.short_description = "Sync console"

    def auto_populate_rules(self, request, queryset):
        from django.conf import settings

        model_labels = list(getattr(settings, "NOOSPHERE_SYNCABLE_MODELS", []))
        if not model_labels:
            self.message_user(request, "NOOSPHERE_SYNCABLE_MODELS is empty.", level=messages.WARNING)
            return

        created_total = 0
        updated_total = 0

        for remote_platform in queryset.select_related("local_platform"):
            for model_label in model_labels:
                try:
                    adapter = get_sync_adapter(model_label)
                    fields = adapter.allowed_fields or []
                except LookupError:
                    fields = []

                rule_name = f"{remote_platform.name}: {model_label} down"
                _, was_created = SyncRule.objects.update_or_create(
                    local_platform=remote_platform.local_platform,
                    remote_platform=remote_platform,
                    model_label=model_label,
                    direction=SyncRule.DIRECTION_DOWN,
                    defaults={
                        "name": rule_name,
                        "enabled": True,
                        "fields": fields,
                        "conflict_policy": SyncRule.CONFLICT_SOURCE_WINS,
                        "sync_creates": True,
                        "sync_updates": True,
                        "sync_deletes": False,
                    },
                )
                if was_created:
                    created_total += 1
                else:
                    updated_total += 1

        self.message_user(
            request,
            f"Sync rules auto-populated. Created: {created_total}, updated: {updated_total}.",
            level=messages.SUCCESS,
        )

    auto_populate_rules.short_description = "Auto-populate sync rules from settings"

    def sync_console_view(self, request, remote_platform_id):
        remote_platform = (
            RemotePlatform.objects
            .select_related("local_platform")
            .get(id=remote_platform_id)
        )

        existing_rules = (
            SyncRule.objects
            .filter(remote_platform=remote_platform)
            .order_by("model_label", "direction")
        )

        form = RemotePlatformSyncConsoleForm()

        if request.method == "POST":
            form = RemotePlatformSyncConsoleForm(request.POST)

            if form.is_valid():
                created = 0
                updated = 0
                ran = 0
                failed = 0

                selected_models = form.cleaned_data["models"]

                filters = {}

                if form.cleaned_data["changed_since_last_sync"]:
                    filters["changed_since_last_sync"] = True

                if form.cleaned_data["only_active"]:
                    filters["only_active"] = True

                for model_label in selected_models:
                    try:
                        adapter = get_sync_adapter(model_label)
                        fields = adapter.allowed_fields or []

                        rule_name = f"{remote_platform.name}: {model_label} down"

                        rule, was_created = SyncRule.objects.update_or_create(
                            local_platform=remote_platform.local_platform,
                            remote_platform=remote_platform,
                            model_label=model_label,
                            direction=SyncRule.DIRECTION_DOWN,
                            defaults={
                                "name": rule_name,
                                "enabled": True,
                                "fields": fields,
                                "filters": filters,
                                "conflict_policy": SyncRule.CONFLICT_SOURCE_WINS,
                                "sync_creates": form.cleaned_data["sync_creates"],
                                "sync_updates": form.cleaned_data["sync_updates"],
                                "sync_deletes": form.cleaned_data["sync_deletes"],
                                "include_dependencies": form.cleaned_data["include_dependencies"],
                            },
                        )

                        if was_created:
                            created += 1
                        else:
                            updated += 1

                        if form.cleaned_data["run_after_create"]:
                            try:
                                from .services import SyncRunner

                                SyncRunner(
                                    local_platform=remote_platform.local_platform,
                                ).run_rule(rule)

                                ran += 1
                            except Exception as exc:
                                failed += 1
                                self.message_user(
                                    request,
                                    f"{rule}: sync failed: {exc}",
                                    level=messages.ERROR,
                                )

                    except Exception as exc:
                        failed += 1
                        self.message_user(
                            request,
                            f"{model_label}: failed: {exc}",
                            level=messages.ERROR,
                        )

                if created or updated:
                    self.message_user(
                        request,
                        f"Sync rules saved. Created: {created}, updated: {updated}.",
                        level=messages.SUCCESS,
                    )

                if ran:
                    self.message_user(
                        request,
                        f"Ran {ran} sync rule(s).",
                        level=messages.SUCCESS,
                    )

                if failed and not created and not updated and not ran:
                    self.message_user(
                        request,
                        "No sync rules were created successfully.",
                        level=messages.ERROR,
                    )

                return redirect(".")

        context = {
            **self.admin_site.each_context(request),
            "title": f"Sync Console: {remote_platform.name}",
            "remote_platform": remote_platform,
            "local_platform": remote_platform.local_platform,
            "form": form,
            "existing_rules": existing_rules,
            "opts": self.model._meta,
        }

        return render(
            request,
            "admin/noosphere/remote_platform_sync_console.html",
            context,
        )


@admin.register(SyncRule)
class SyncRuleAdmin(TotoModelAdmin):
    list_display = (
        "name",
        "local_platform",
        "remote_platform",
        "model_label",
        "enabled",
        "sync_creates",
        "sync_updates",
        "sync_deletes",
        "last_pulled_at",
        "remote_preview_link",
    )

    list_filter = (
        "enabled",
        "sync_creates",
        "sync_updates",
        "sync_deletes",
        "local_platform",
        "remote_platform",
    )

    search_fields = (
        "name",
        "model_label",
        "local_platform__site_name",
        "local_platform__domain",
        "remote_platform__name",
        "remote_platform__base_url",
    )

    readonly_fields = (
        "created_at",
        "updated_at",
        "last_pulled_at",
        "remote_preview_link",
    )

    fieldsets = (
        (None, {
            "fields": (
                "local_platform",
                "remote_platform",
                "name",
                "enabled",
                "model_label",
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
                "last_pulled_at",
                "created_at",
                "updated_at",
            )
        }),
        ("Remote preview", {
            "fields": (
                "remote_preview_link",
            )
        }),
    )

    actions = (
        "run_selected_rules",
    )

    def get_urls(self):
        return [
            path(
                "<int:rule_id>/remote-preview/",
                self.admin_site.admin_view(self.remote_preview_view),
                name="noosphere_syncrule_remote_preview",
            ),
        ] + super().get_urls()

    def remote_preview_link(self, obj):
        if not obj or not obj.pk:
            return "-"

        url = reverse("admin:noosphere_syncrule_remote_preview", args=[obj.pk])
        return format_html('<a class="button" href="{}">View remote data</a>', url)

    remote_preview_link.short_description = "Remote preview"

    def remote_preview_view(self, request, rule_id):
        from .remote import RemotePreviewClient

        rule = (
            SyncRule.objects
            .select_related("local_platform", "remote_platform")
            .get(pk=rule_id)
        )

        try:
            rows = RemotePreviewClient(rule=rule).list_objects()
            error = None
        except Exception as exc:
            rows = []
            error = str(exc)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Remote preview: {rule}",
            "rule": rule,
            "rows": rows,
            "error": error,
            "opts": self.model._meta,
        }

        return render(request, "admin/noosphere/remote_preview.html", context)

    def run_selected_rules(self, request, queryset):
        ran = 0
        failed = 0

        for rule in (
            queryset
            .select_related("local_platform", "remote_platform")
            .filter(enabled=True)
        ):
            try:
                from .services import SyncRunner

                SyncRunner(local_platform=rule.local_platform).run_rule(rule)
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
        "local_platform",
        "remote_platform",
        "rule",
        "status",
        "started_at",
        "finished_at",
        "imported_count",
        "created_count",
        "updated_count",
        "skipped_count",
        "deleted_count",
        "failed_count",
    )

    list_filter = (
        "status",
        "local_platform",
        "remote_platform",
        "started_at",
    )

    search_fields = (
        "rule__name",
        "rule__model_label",
        "local_platform__site_name",
        "local_platform__domain",
        "remote_platform__name",
        "remote_platform__base_url",
        "message",
        "package_hash",
    )

    readonly_fields = (
        "local_platform",
        "remote_platform",
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

    def has_add_permission(self, request):
        return False
