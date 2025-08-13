from django.contrib import admin
from .models import Template, Page, Image
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'created_at')
    search_fields = ('name', 'description', 'content', 'header')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }

    list_display = ('slug', 'template', 'author', 'language', 'created_at')
    search_fields = ('slug', 'language', 'author__username', 'template__name')
    list_filter = ('language', 'created_at', 'template')
    ordering = ('-created_at',)
    autocomplete_fields = ('template', 'author')
    readonly_fields = ('created_at', )


@admin.register(Image)
class ImageAdmin(admin.ModelAdmin):
    list_display = ('name', 'author', 'slug', 'created_at')
    search_fields = ('name', 'slug', 'author__username')
    readonly_fields = ('created_at', )
    autocomplete_fields = ('author',)
