from django.contrib import admin, messages
from .models import Template, Page, TemplateSource, TemplateGenerator
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField
from django.urls import reverse
from django.utils.html import format_html
import os
from django import forms
from .models import TemplateGenerator


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

class TemplateSourceForm(forms.ModelForm):
    class Meta:
        model = TemplateSource
        fields = '__all__'
        widgets = {
            'token': forms.PasswordInput(render_value=True),
        }

@admin.register(TemplateSource)
class TemplateSourceAdmin(admin.ModelAdmin):
    form = TemplateSourceForm

    list_display = ('name', 'repo_url', 'branch')
    search_fields = ('name', 'repo_url', 'branch')
    actions = ['pull_repo']

    def pull_repo(self, request, queryset):
        success_count = 0
        failure_count = 0

        for source in queryset:
            try:
                source.pull_repo()
                success_count += 1
            except Exception as e:
                failure_count += 1
                self.message_user(
                    request,
                    f"Failed to pull '{source.name}': {str(e)}",
                    level=messages.ERROR
                )

        if success_count:
            self.message_user(
                request,
                f"Successfully pulled {success_count} template source(s).",
                level=messages.SUCCESS
            )

    pull_repo.short_description = "Pull selected repositories"


class TemplateGeneratorForm(forms.ModelForm):
    class Meta:
        model = TemplateGenerator
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        source = self.instance.source if self.instance.pk else None
        choices = []

        if source:
            try:
                repo_path = source.get_local_path()
                source.pull_repo()  # Ensure latest repo content

                for root, dirs, files in os.walk(repo_path):
                    # Exclude hidden directories
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
                    # Exclude hidden files
                    files = [f for f in files if not f.startswith('.')]

                    for file in files:
                        rel_path = os.path.relpath(os.path.join(root, file), repo_path)
                        choices.append((rel_path, rel_path))

                if choices:
                    self.fields['file_path'] = forms.ChoiceField(
                        choices=choices,
                        required=True,
                        help_text="Select a file from the repository"
                    )
                else:
                    self.fields['file_path'] = forms.CharField(
                        required=True,
                        help_text="No visible files found in the repository"
                    )

            except Exception as e:
                self.fields['file_path'] = forms.CharField(
                    required=True,
                    help_text=f"Error accessing repo: {str(e)}"
                )
        else:
            self.fields['file_path'] = forms.CharField(
                required=True,
                help_text="Save the form with a source first to choose a file"
            )


@admin.register(TemplateGenerator)
class TemplateGeneratorAdmin(admin.ModelAdmin):
    form = TemplateGeneratorForm
    list_display = ('target', 'source', 'file_path', 'last_synced')
    autocomplete_fields = ('target', 'source')
    readonly_fields = ('last_synced',)
    actions = ['generate']

    def generate(self, request, queryset):
        success_count = 0
        failure_count = 0

        for generator in queryset:
            try:
                generator.sync_template()
                success_count += 1
            except Exception as e:
                failure_count += 1
                self.message_user(
                    request,
                    f"Failed to generate template from '{generator.file_path}' in '{generator.source.name}': {str(e)}",
                    level=messages.ERROR
                )

        if success_count:
            self.message_user(
                request,
                f"Successfully generated {success_count} template(s).",
                level=messages.SUCCESS
            )

    generate.short_description = "Generate template from selected file(s)"


# @admin.register(Image)
# class ImageAdmin(admin.ModelAdmin):
#     list_display = ('name', 'author', 'slug', 'created_at')
#     search_fields = ('name', 'slug', 'author__username')
#     readonly_fields = ('created_at', )
#     autocomplete_fields = ('author',)
