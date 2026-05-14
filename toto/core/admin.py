import os
import tempfile

from django.conf import settings
from django.contrib import admin, messages
from django.http import FileResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html

from toto.core.base_admin import TotoModelAdmin
from .forms import BackupAppsForm, ApplyBackupForm, QueryExecForm
from .models import Platform, Font, Theme, ColorMix, Federation
from .services.backup_service import BackupService
from .services.sync_service import SyncService


@admin.register(Font)
class FontAdmin(TotoModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


@admin.register(ColorMix)
class ColorMixAdmin(TotoModelAdmin):
    list_display = ('name', 'preview_light', 'preview_dark')
    readonly_fields = ('preview_light', 'preview_dark')

    fieldsets = (
        (None, {'fields': ('name',)}),
        ('Light Mode Colors', {
            'fields': (
                'primary_bg_light', 'text_main_light',
                'header_bg_light', 'bubble_bg_light',
                'appbar_bg_light', 'appbar_text_light',
                'footer_bg_light', 'footer_text_light',
                'accent_light', 'warn_light',
                'success_light', 'sunken_light', 'link_light',
                'preview_light',
            )
        }),
        ('Dark Mode Colors', {
            'fields': (
                'primary_bg_dark', 'text_main_dark',
                'header_bg_dark', 'bubble_bg_dark',
                'appbar_bg_dark', 'appbar_text_dark',
                'footer_bg_dark', 'footer_text_dark',
                'accent_dark', 'warn_dark',
                'success_dark', 'sunken_dark', 'link_dark',
                'preview_dark',
            )
        }),
        ('Accent Colors', {'fields': ('accent_1', 'accent_2')}),
    )

    def preview_light(self, obj):
        return self._render_preview(
            obj.primary_bg_light, obj.bubble_bg_light,
            obj.text_main_light, obj.accent_light,
            obj.footer_bg_light, obj.footer_text_light, "Light",
        )
    preview_light.short_description = "Light Preview"

    def preview_dark(self, obj):
        return self._render_preview(
            obj.primary_bg_dark, obj.bubble_bg_dark,
            obj.text_main_dark, obj.accent_dark,
            obj.footer_bg_dark, obj.footer_text_dark, "Dark",
        )
    preview_dark.short_description = "Dark Preview"

    def _render_preview(self, bg, bubble, text, accent, footer_bg, footer_text, label):
        return format_html(
            '''<div style="display:flex;gap:8px;flex-wrap:wrap;">
                <div style="background-color:{bg};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">{label} BG</div>
                <div style="background-color:{bubble};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">Bubble</div>
                <div style="background-color:{accent};color:{text};padding:8px;border-radius:4px;width:120px;text-align:center;">Accent {label}</div>
            </div>''',
            bg=bg, bubble=bubble, text=text, accent=accent, label=label,
        )


@admin.register(Theme)
class ThemeAdmin(TotoModelAdmin):
    list_display = ('name', 'font')
    search_fields = ('name', 'font__name')
    ordering = ('name',)


@admin.register(Federation)
class FederationAdmin(TotoModelAdmin):
    list_display = ("name", "active", "created_at")
    search_fields = ("name",)
    list_filter = ("active", "created_at")


@admin.register(Platform)
class PlatformAdmin(TotoModelAdmin):
    list_display = (
        'site_name', 'domain', 'publication_year', 'active',
        'get_theme_name', 'rate_limit_window', 'rate_limit_max_requests',
    )
    search_fields = ('site_name', 'domain', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']
    actions = ["backup_console_action"]

    def get_theme_name(self, obj):
        return obj.theme.name if obj.theme else '-'
    get_theme_name.short_description = 'Theme'

    def get_urls(self):
        return [
            path(
                "backup-console/<int:platform_id>/",
                self.admin_site.admin_view(self.backup_console_view),
                name="platform_backup_console",
            ),
        ] + super().get_urls()

    def backup_console_action(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(request, "Select exactly one platform.", level=messages.ERROR)
            return
        return redirect(f"backup-console/{queryset.first().id}/")

    backup_console_action.short_description = "Backup / seed console"

    # ---------------------------------------------------------
    # Backup console view — all boxes on one page
    # ---------------------------------------------------------

    def backup_console_view(self, request, platform_id):
        platform = Platform.objects.get(id=platform_id)
        apps_choices = getattr(settings, "APPS_TO_SYNC", [])

        backup_form = BackupAppsForm(apps_choices=apps_choices, initial={"apps": apps_choices})
        apply_form = ApplyBackupForm()
        query_form = QueryExecForm()
        query_result = None

        if request.method == "POST":
            action = request.POST.get("action")

            if action == "create_backup":
                backup_form = BackupAppsForm(request.POST, apps_choices=apps_choices)
                if backup_form.is_valid():
                    try:
                        service = BackupService(
                            platform=platform,
                            apps_to_sync=backup_form.cleaned_data["apps"],
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

            elif action == "apply_backup":
                apply_form = ApplyBackupForm(request.POST, request.FILES)
                if apply_form.is_valid():
                    tmp_path = None
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
                            for chunk in apply_form.cleaned_data["backup_file"].chunks():
                                tmp.write(chunk)
                            tmp_path = tmp.name
                        SyncService(platform=platform, apps_to_sync=apps_choices).apply_backup(
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

            elif action == "exec_query":
                query_form = QueryExecForm(request.POST)
                if query_form.is_valid():
                    try:
                        query_result = self._exec_query(platform, query_form.cleaned_data["query"])
                    except Exception as e:
                        self.message_user(request, f"Query failed: {e}", level=messages.ERROR)

        context = {
            **self.admin_site.each_context(request),
            "title": f"Backup Console: {platform.site_name}",
            "platform": platform,
            "backup_form": backup_form,
            "apply_form": apply_form,
            "query_form": query_form,
            "query_result": query_result,
        }
        return render(request, "admin/platform_backup_console.html", context)

    def _exec_query(self, platform, query):
        q = query.strip().lower()
        if q == "show apps":
            return getattr(settings, "APPS_TO_SYNC", [])
        if q == "show models":
            from django.apps import apps
            result = []
            for app_label in getattr(settings, "APPS_TO_SYNC", []):
                for model in apps.get_app_config(app_label).get_models():
                    if hasattr(model, "uid"):
                        result.append(model._meta.label)
            return result
        if q == "show platform":
            return {
                "id": platform.id,
                "site_name": platform.site_name,
                "domain": platform.domain,
                "active": platform.active,
                "api_url": platform.api_url,
            }
        raise ValueError("Unknown query. Try: show apps | show models | show platform")
