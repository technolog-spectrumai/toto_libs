from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from django.urls import reverse
from django.utils.html import format_html
from django.template import TemplateDoesNotExist
from .models import Language, StaticPage, DynamicPage, Image, PageGenerator


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'language')
    search_fields = ('name', 'slug')
    list_filter = ('language',)


@admin.register(PageGenerator)
class PageGeneratorAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }

    readonly_fields = ('render_check', 'schema_check')

    def full_url(self, obj):
        try:
            url = reverse('dynamic_page', kwargs={'slug': obj.slug, 'lang': obj.language.slug})
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        except Exception:
            return "Invalid URL"
    full_url.short_description = "Page URL"

    def generator_name(self, obj):
        return obj.generator.name
    generator_name.short_description = "Generator"

    def render_check(self, obj):
        try:
            obj.render_to_string()
            return format_html('<span style="color:green;">Rendered successfully</span>')
        except TemplateDoesNotExist:
            return format_html('<span style="color:red;">Template not found</span>')
        except Exception as e:
            return format_html('<span style="color:red;">Render error - {}</span>', str(e))
    render_check.short_description = "Render Status"

    def schema_check(self, obj):
        import jsonschema
        try:
            jsonschema.validate(instance=obj.config_json, schema=obj.generator.json_schema)
            return format_html('<span style="color:green;">Valid</span>')
        except jsonschema.ValidationError as e:
            return format_html('<span style="color:red;">{}</span>', e.message)
        except Exception as e:
            return format_html('<span style="color:red;">Schema error</span>')
    schema_check.short_description = "Schema Validation"

    list_display = ('name', 'slug', 'language', 'generator_name', 'full_url')
    search_fields = ('name', 'slug', 'generator__name')
    list_filter = ('language', 'generator')



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
