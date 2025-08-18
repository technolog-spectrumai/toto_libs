from django.contrib import admin
from .models import Platform, DashboardBlock, Font, Theme, AppIngress
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField


@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


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
            result = ingress.run_ingress_command()
            self.message_user(request, result)

