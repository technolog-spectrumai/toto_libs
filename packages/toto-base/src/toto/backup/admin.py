import os
import tempfile

from django.conf import settings
from django.contrib import admin, messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import path

from .forms import BackupAppsForm, ApplyBackupForm
from .models import BackupProfile, StoredBackup
from .services.backup_service import BackupService
from .services.stored_backup_service import StoredBackupService
from .services.sync_service import SyncService


@admin.register(BackupProfile)
class BackupProfileAdmin(admin.ModelAdmin):
    list_display = ("platform", "signing_key", "has_verify_key")
    search_fields = ("platform__site_name",)
    autocomplete_fields = ("platform",)

    @admin.display(boolean=True, description="Verify key set")
    def has_verify_key(self, obj):
        return bool(obj.verify_key)


@admin.register(StoredBackup)
class StoredBackupAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "platform",
        "is_active",
        "expires_at",
        "size_bytes",
        "pull_count",
        "last_pulled_at",
        "created_at",
    )
    list_filter = ("is_active", "platform", "created_at", "expires_at")
    search_fields = ("name", "uid", "sha256", "platform__site_name")
    readonly_fields = (
        "uid",
        "magic_token",
        "api_key_hint",
        "sha256",
        "size_bytes",
        "pull_count",
        "last_pulled_at",
        "created_at",
        "pull_path_display",
    )
    autocomplete_fields = ("platform", "created_by")
    fieldsets = (
        (None, {
            "fields": ("name", "platform", "file", "apps", "is_active", "expires_at", "created_by"),
        }),
        ("Pull API", {
            "fields": ("pull_path_display", "uid", "magic_token", "api_key_hint"),
            "description": "Remote clients pull with Authorization: Bearer <api key> or X-Api-Key.",
        }),
        ("Integrity", {
            "fields": ("sha256", "size_bytes"),
        }),
        ("Audit", {
            "fields": ("pull_count", "last_pulled_at", "created_at"),
        }),
    )

    @admin.display(description="Pull URL path")
    def pull_path_display(self, obj):
        if not obj.pk:
            return "-"
        return obj.pull_path()


class BackupAdminMixin:
    """
    Mix this into PlatformAdmin to add the backup and seed console views.
    Keeps all backup logic outside of toto.core.
    """

    def get_urls(self):
        return [
            path(
                "backup-console/<int:platform_id>/",
                self.admin_site.admin_view(self.backup_console_view),
                name="platform_backup_console",
            ),
            path(
                "seed-console/<int:platform_id>/",
                self.admin_site.admin_view(self.seed_console_view),
                name="platform_seed_console",
            ),
        ] + super().get_urls()

    def backup_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one platform.", level=messages.ERROR)
            return
        return redirect(f"backup-console/{queryset.first().id}/")

    backup_console_action.short_description = "Backup console"

    def seed_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one platform.", level=messages.ERROR)
            return
        return redirect(f"seed-console/{queryset.first().id}/")

    seed_console_action.short_description = "Seed console"

    def backup_console_view(self, request, platform_id):
        from toto.core.models import Platform
        platform = Platform.objects.get(id=platform_id)
        apps_choices = getattr(settings, "APPS_TO_SYNC", [])

        backup_form = BackupAppsForm(apps_choices=apps_choices, initial={"apps": apps_choices})

        if request.method == "POST":
            backup_form = BackupAppsForm(request.POST, apps_choices=apps_choices)
            if backup_form.is_valid():
                sign = request.POST.get("sign_backup") == "1"
                try:
                    apps_to_sync = backup_form.cleaned_data["apps"]
                    if request.POST.get("store_backup") == "1":
                        stored_backup, raw_api_key = StoredBackupService(
                            platform=platform,
                            apps_to_sync=apps_to_sync,
                            sign=sign,
                            created_by=request.user,
                        ).create()
                        self.message_user(
                            request,
                            "Stored backup created. "
                            f"Pull URL: {stored_backup.pull_url(request)} "
                            f"API key: {raw_api_key}",
                            level=messages.SUCCESS,
                        )
                        return redirect(".")
                    service = BackupService(
                        platform=platform,
                        apps_to_sync=apps_to_sync,
                        sign=sign,
                    )
                    backup_path = service.create_backup()
                except Exception as e:
                    self.message_user(request, f"Backup failed: {e}", level=messages.ERROR)
                    return redirect(".")
                return FileResponse(
                    open(backup_path, "rb"),
                    as_attachment=True,
                    filename=backup_path.name,
                )

        context = {
            **self.admin_site.each_context(request),
            "title": f"Backup Console: {platform.site_name}",
            "platform": platform,
            "backup_form": backup_form,
        }
        return render(request, "admin/platform_backup_console.html", context)

    def seed_console_view(self, request, platform_id):
        from toto.core.models import Platform
        platform = Platform.objects.get(id=platform_id)
        apply_form = ApplyBackupForm()

        if request.method == "POST":
            apply_form = ApplyBackupForm(request.POST, request.FILES)
            if apply_form.is_valid():
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                        for chunk in apply_form.cleaned_data["backup_file"].chunks():
                            tmp.write(chunk)
                        tmp_path = tmp.name
                    SyncService(platform=platform).apply_backup(
                        backup_path=tmp_path,
                        verify_signature=apply_form.cleaned_data["verify_signature"],
                        clear_existing=apply_form.cleaned_data["clear_existing"],
                    )
                    self.message_user(request, "Backup applied successfully.")
                    return redirect(".")
                except Exception as e:
                    self.message_user(request, f"Apply backup failed: {e}", level=messages.ERROR)
                finally:
                    if tmp_path:
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass

        context = {
            **self.admin_site.each_context(request),
            "title": f"Seed Console: {platform.site_name}",
            "platform": platform,
            "apply_form": apply_form,
        }
        return render(request, "admin/platform_seed_console.html", context)
