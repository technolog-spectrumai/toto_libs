
from .models import Platform, DashboardBlock, Font, Theme, AppIngress, ColorMix
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField
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
                'header_bg_light', 'appbar_bg_light', 'bubble_bg_light',
                'accent_light', 'warn_light',
                'preview_light'
            )
        }),
        ('Dark Mode Colors', {
            'fields': (
                'primary_bg_dark', 'text_main_dark',
                'header_bg_dark', 'appbar_bg_dark', 'bubble_bg_dark',
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
            label="Light"
        )

    preview_light.short_description = "Light Preview"

    def preview_dark(self, obj):
        return self._render_preview_set(
            bg=obj.primary_bg_dark,
            bubble=obj.bubble_bg_dark,
            text=obj.text_main_dark,
            accent_mode=obj.accent_dark,
            label="Dark"
        )

    preview_dark.short_description = "Dark Preview"

    def _render_preview_set(self, bg, bubble, text, accent_mode, label):
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

@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ('site_name', 'domain', 'publication_year', 'active', 'get_theme_name')
    search_fields = ('site_name', 'domain', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']

    def get_theme_name(self, obj):
        return obj.theme.name if obj.theme else '-'
    get_theme_name.short_description = 'Theme'


@admin.register(DashboardBlock)
class DashboardBlockAdmin(admin.ModelAdmin):
    list_display = ('title', 'icon', 'description', 'link')
    search_fields = ('title', 'description', 'icon', 'link')
    ordering = ('title',)


@admin.register(AppIngress)
class AppIngressAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    list_display = ("app_name", "scheduled_at")
    actions = ["run_ingress"]


    @admin.action(description="Run ingress command for selected entries")
    def run_ingress(self, request, queryset):
        for ingress in queryset:
            result, code = ingress.run_ingress_command()

            # Map return code to Django message level
            if code == 0:
                level = messages.SUCCESS
            elif code == -1:
                level = messages.WARNING
            elif code == 1:
                level = messages.ERROR
            else:
                level = messages.INFO

            self.message_user(request, result, level=level)

