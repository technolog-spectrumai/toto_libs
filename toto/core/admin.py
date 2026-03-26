from .models import Platform, DashboardBlock, Font, Theme, ColorMix, Federation
from django.contrib import admin, messages
from django.utils.html import format_html


@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


@admin.register(ColorMix)
class ColorMixAdmin(admin.ModelAdmin):
    list_display = ('name', 'preview_light', 'preview_dark')
    readonly_fields = ('preview_light', 'preview_dark')

    fieldsets = (
        (None, {
            'fields': ('name',)
        }),
        ('Light Mode Colors', {
            'fields': (
                'primary_bg_light', 'text_main_light',
                'header_bg_light', 'bubble_bg_light',
                'appbar_bg_light', 'appbar_text_light',
                'footer_bg_light', 'footer_text_light',
                'accent_light', 'warn_light',
                'preview_light'
            )
        }),
        ('Dark Mode Colors', {
            'fields': (
                'primary_bg_dark', 'text_main_dark',
                'header_bg_dark', 'bubble_bg_dark',
                'appbar_bg_dark', 'appbar_text_dark',
                'footer_bg_dark', 'footer_text_dark',
                'accent_dark', 'warn_dark',
                'preview_dark'
            )
        }),
        ('Accent Colors', {
            'fields': ('accent_1', 'accent_2')
        }),
    )

    def preview_light(self, obj):
        return self._render_preview_set(
            bg=obj.primary_bg_light,
            bubble=obj.bubble_bg_light,
            text=obj.text_main_light,
            accent_mode=obj.accent_light,
            footer_bg=obj.footer_bg_light,
            footer_text=obj.footer_text_light,
            label="Light"
        )

    preview_light.short_description = "Light Preview"

    def preview_dark(self, obj):
        return self._render_preview_set(
            bg=obj.primary_bg_dark,
            bubble=obj.bubble_bg_dark,
            text=obj.text_main_dark,
            accent_mode=obj.accent_dark,
            footer_bg=obj.footer_bg_dark,
            footer_text=obj.footer_text_dark,
            label="Dark"
        )

    preview_dark.short_description = "Dark Preview"

    def _render_preview_set(self, bg, bubble, text, accent_mode, footer_bg, footer_text, label):
        return format_html(
            '''
            <div style="display: flex; gap: 8px; flex-wrap: wrap;">
                <div style="background-color:{bg}; color:{text}; padding:8px; border-radius:4px; width:120px; text-align:center;">
                    {label} BG
                </div>
                <div style="background-color:{bubble}; color:{text}; padding:8px; border-radius:4px; width:120px; text-align:center;">
                    Bubble
                </div>
                <div style="background-color:{accent}; color:{text}; padding:8px; border-radius:4px; width:120px; text-align:center;">
                    Accent {label}
                </div>
            </div>
            ''',
            bg=bg,
            bubble=bubble,
            text=text,
            accent=accent_mode,
            label=label
        )


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ('name', 'font')
    search_fields = ('name', 'font__name')
    ordering = ('name',)


@admin.register(Federation)
class FederationAdmin(admin.ModelAdmin):
    list_display = ("name", "active", "created_at")
    search_fields = ("name",)
    list_filter = ("active", "created_at")


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = (
        'site_name',
        'domain',
        'publication_year',
        'active',
        'get_theme_name',
        'rate_limit_window',
        'rate_limit_max_requests',
    )
    search_fields = ('site_name', 'domain', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']

    def get_theme_name(self, obj):
        return obj.theme.name if obj.theme else '-'
    get_theme_name.short_description = 'Theme'


@admin.register(DashboardBlock)
class DashboardBlockAdmin(admin.ModelAdmin):
    list_display = ('title', 'icon', 'description', 'link', 'public')
    search_fields = ('title', 'description', 'icon', 'link')
    ordering = ('title',)