from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from django.urls import reverse
from django.utils.html import format_html
from .models import Language, StaticPage, DynamicPage, Image


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'language')
    search_fields = ('name', 'slug')
    list_filter = ('language',)


@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }

    def full_url(self, obj):
        try:
            url = reverse('dynamic_page', kwargs={'slug': obj.slug, 'lang': obj.language.slug})
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        except Exception:
            return "Invalid URL"

    full_url.short_description = "Page URL"

    list_display = ('name', 'slug', 'language', 'template_key', 'full_url')
    search_fields = ('name', 'slug', 'template_key')
    list_filter = ('language', 'template_key')


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'created_at', 'full_url')
    search_fields = ('name', 'slug')
    readonly_fields = ('created_at', )

    def full_url(self, obj):
        try:
            url = reverse('image_url', kwargs={'slug': obj.slug})
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        except Exception:
            return "Invalid URL"

    full_url.short_description = "Image URL"