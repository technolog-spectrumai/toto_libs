from django.contrib import admin
from django.utils.html import format_html

from toto.core.base_admin import TotoModelAdmin
from toto.backup.admin import BackupAdminMixin
from .models import Platform, Font, Theme, ColorMix, Federation


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
class PlatformAdmin(BackupAdminMixin, TotoModelAdmin):
    list_display = (
        'site_name', 'domain', 'publication_year', 'active',
        'get_theme_name', 'rate_limit_window', 'rate_limit_max_requests',
    )
    search_fields = ('site_name', 'domain', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']
    actions = ["backup_console_action", "seed_console_action"]

    def get_theme_name(self, obj):
        return obj.theme.name if obj.theme else '-'
    get_theme_name.short_description = 'Theme'
