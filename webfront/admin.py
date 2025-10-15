from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from django.urls import reverse
from django.utils.html import format_html
from django.template import TemplateDoesNotExist
from .models import Language, StaticPage, DynamicPage, Image, PageGenerator
from django import forms
from django.core.files.base import ContentFile
from django_ace import AceWidget
from .batch import BatchAction


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


class HtmlFileMixin:
    def read_file_content(self, file_field):
        """Safely read and decode file content from a FileField."""
        if file_field and hasattr(file_field, 'read'):
            try:
                return file_field.read().decode('utf-8')
            except Exception:
                return ''
        return ''

    def save_file_content(self, instance, file_field_name, slug, content):
        """Save string content to a FileField on the instance."""
        if content:
            filename = f"{slug}.html"
            file_field = getattr(instance, file_field_name, None)
            if file_field:
                file_field.save(filename, ContentFile(content), save=False)


class StaticPageAdminForm(forms.ModelForm, HtmlFileMixin):
    html_content = forms.CharField(
        widget=AceWidget(mode='html', theme='chrome'),
        required=False,
        label="HTML Content"
    )

    class Meta:
        model = StaticPage
        fields = ('name', 'slug', 'language', 'html_file')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['html_content'].initial = self.read_file_content(self.instance.html_file)


    def save(self, commit=True):
        instance = super().save(commit=False)
        self.save_file_content(instance, 'html_file', instance.slug, self.cleaned_data.get('html_content', ''))
        if commit:
            instance.save()
        return instance


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    form = StaticPageAdminForm
    list_display = ('name', 'slug', 'language', 'full_url')
    search_fields = ('name', 'slug')
    list_filter = ('language',)

    def full_url(self, obj):
        try:
            url = reverse('static_page', kwargs={'slug': obj.slug, 'lang': obj.language.slug})
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        except Exception:
            return "Invalid URL"


class PageGeneratorAdminForm(forms.ModelForm, HtmlFileMixin):
    template_content = forms.CharField(
        widget=AceWidget(mode='html', theme='chrome'),
        required=False,
        label="Template HTML"
    )

    class Meta:
        model = PageGenerator
        fields = ('name', 'slug', 'html_template_file', 'template_content', 'json_schema', 'check_schema')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['template_content'].initial = self.read_file_content(self.instance.html_template_file)


    def save(self, commit=True):
        instance = super().save(commit=False)
        self.save_file_content(instance, 'html_template_file', instance.slug, self.cleaned_data.get('template_content', ''))
        if commit:
            instance.save()
        return instance


@admin.register(PageGenerator)
class PageGeneratorAdmin(admin.ModelAdmin):
    form = PageGeneratorAdminForm
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
    actions = ['bake_to_static_action']

    def bake_to_static_action(self, request, queryset):
        def operation(page):
            return page.bake_to_static()

        result = BatchAction(queryset).run(operation)
        BatchAction.display_messages(result, self.message_user, request, verb="bake")

    bake_to_static_action.short_description = "Bake selected dynamic pages to static"

    readonly_fields = ('render_check', 'schema_check')

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

    list_display = ('name', 'slug', 'language', 'generator_name', 'render_check', 'schema_check')
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
