from django.contrib import admin
from django.db.models import JSONField
from django_json_widget.widgets import JSONEditorWidget
from django.urls import reverse
from django.utils.html import format_html
from django.template import TemplateDoesNotExist
from .models import Language, WebPage, PageGenerator, PageTemplate
from django import forms
from django.core.files.base import ContentFile
from django_ace import AceWidget
from .batch import BatchAction


@admin.register(Language)
class LanguageAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


class VaultHtmlMixin:
    def read_file_content(self, vault_file):
        if vault_file and vault_file.file:
            try:
                return vault_file.file.read().decode('utf-8')
            except Exception:
                return ''
        return ''

    def save_file_content(self, vault_file, slug, content):
        if vault_file and content:
            vault_file.file.save(f"{slug}.html", ContentFile(content))
            vault_file.save()


class WebPageAdminForm(forms.ModelForm, VaultHtmlMixin):
    html_content = forms.CharField(
        widget=AceWidget(mode='html', theme='chrome'),
        required=False,
        label="HTML Content"
    )

    class Meta:
        model = WebPage
        fields = ('name', 'slug', 'language', 'html_file')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['html_content'].initial = self.read_file_content(self.instance.html_file)

    def save(self, commit=True):
        instance = super().save(commit=False)
        content = self.cleaned_data.get('html_content', '')
        if instance.html_file:
            self.save_file_content(instance.html_file, instance.slug, content)
        elif content:
            from vault.models import VaultFile
            vault_file = VaultFile.objects.create(
                owner=instance.language,
                title=f"{instance.name}.html",
                file_type='text',
                notes=f"Uploaded via admin for WebPage {instance.slug}"
            )
            vault_file.file.save(f"{instance.slug}.html", ContentFile(content))
            vault_file.save()
            instance.html_file = vault_file
        if commit:
            instance.save()
        return instance


@admin.register(WebPage)
class WebPageAdmin(admin.ModelAdmin):
    form = WebPageAdminForm
    list_display = ('name', 'slug', 'language', 'full_url')
    search_fields = ('name', 'slug')
    list_filter = ('language',)

    def full_url(self, obj):
        try:
            url = reverse('static_page', kwargs={'slug': obj.slug, 'lang': obj.language.slug})
            return format_html('<a href="{}" target="_blank">{}</a>', url, url)
        except Exception:
            return "Invalid URL"


class PageTemplateAdminForm(forms.ModelForm, VaultHtmlMixin):
    template_content = forms.CharField(
        widget=AceWidget(mode='html', theme='chrome'),
        required=False,
        label="Template HTML"
    )

    class Meta:
        model = PageTemplate
        fields = ('name', 'slug', 'html_template_file', 'template_content', 'json_schema', 'check_schema')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['template_content'].initial = self.read_file_content(self.instance.html_template_file)

    def save(self, commit=True):
        instance = super().save(commit=False)
        content = self.cleaned_data.get('template_content', '')
        if instance.html_template_file:
            self.save_file_content(instance.html_template_file, instance.slug, content)
        elif content:
            from vault.models import VaultFile
            vault_file = VaultFile.objects.create(
                owner=None,
                title=f"{instance.name}_template.html",
                file_type='text',
                notes=f"Uploaded via admin for PageTemplate {instance.slug}"
            )
            vault_file.file.save(f"{instance.slug}_template.html", ContentFile(content))
            vault_file.save()
            instance.html_template_file = vault_file
        if commit:
            instance.save()
        return instance


@admin.register(PageTemplate)
class PageTemplateAdmin(admin.ModelAdmin):
    form = PageTemplateAdminForm
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    list_display = ('name', 'slug')
    search_fields = ('name', 'slug')


@admin.register(PageGenerator)
class PageGeneratorAdmin(admin.ModelAdmin):
    formfield_overrides = {
        JSONField: {'widget': JSONEditorWidget}
    }
    actions = ['bake_to_static_action']

    def bake_to_static_action(self, request, queryset):
        def operation(page):
            return page.bake_to_static()

        result = BatchAction(queryset).run(operation)
        BatchAction.display_messages(result, self.message_user, request, verb="bake")

    bake_to_static_action.short_description = "Bake selected page generators to static"

    readonly_fields = ('render_check', 'schema_check')

    def template_name(self, obj):
        return obj.template.name
    template_name.short_description = "Template"

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
            jsonschema.validate(instance=obj.config_json, schema=obj.template.json_schema)
            return format_html('<span style="color:green;">Valid</span>')
        except jsonschema.ValidationError as e:
            return format_html('<span style="color:red;">{}</span>', e.message)
        except Exception as e:
            return format_html('<span style="color:red;">Schema error</span>')
    schema_check.short_description = "Schema Validation"

    list_display = ('name', 'slug', 'language', 'template_name', 'render_check', 'schema_check')
    search_fields = ('name', 'slug', 'template__name')
    list_filter = ('language', 'template')