from django.contrib import admin
from .models import Template, Page
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField
from django.urls import reverse
from django.utils.html import format_html
from django.conf import settings


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name', 'description', 'content')  # Removed 'header'
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }

    list_display = ('slug', 'template', 'author', 'language', 'created_at', 'full_url')
    search_fields = ('slug', 'language', 'author__username', 'template__name')
    list_filter = ('language', 'created_at', 'template')
    ordering = ('-created_at',)
    autocomplete_fields = ('template', 'author')
    readonly_fields = ('created_at',)

    def full_url(self, obj):
        try:
            url = reverse('page_detail', kwargs={'slug': obj.slug, 'language': obj.language})
            full_url = f"{url}"
            return format_html('<a href="{}" target="_blank">{}</a>', full_url, full_url)
        except Exception:
            return "Invalid URL"

    full_url.short_description = "Page URL"


# @admin.register(Image)
# class ImageAdmin(admin.ModelAdmin):
#     list_display = ('name', 'author', 'slug', 'created_at')
#     search_fields = ('name', 'slug', 'author__username')
#     readonly_fields = ('created_at', )
#     autocomplete_fields = ('author',)
