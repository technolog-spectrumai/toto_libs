from django.contrib import admin, messages
from .models import Template, Page, Repository, Codebase, TemplateArtifact
from django_json_widget.widgets import JSONEditorWidget
from django.db.models import JSONField
from django.urls import reverse
from django.utils.html import format_html
import os
from django import forms


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

class RepositoryForm(forms.ModelForm):
    class Meta:
        model = Repository
        fields = '__all__'
        widgets = {
            'token': forms.PasswordInput(render_value=True),
        }

@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    form = RepositoryForm

    list_display = ('name', 'repo_url')
    search_fields = ('name', 'repo_url')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        if not request.user.is_superuser:
            form.base_fields['token'].widget = forms.HiddenInput()
        return form


@admin.register(Codebase)
class CodebaseAdmin(admin.ModelAdmin):
    list_display = ('repo', 'branch', 'last_synced')
    search_fields = ('repo__name', 'branch')
    autocomplete_fields = ('repo',)
    readonly_fields = ('last_synced',)
    actions = ['pull_codebases']

    def pull_codebases(self, request, queryset):
        success_count = 0
        failure_count = 0

        for codebase in queryset:
            try:
                codebase.update_codebase()
                success_count += 1
            except Exception as e:
                failure_count += 1
                self.message_user(
                    request,
                    f"Failed to pull'{codebase.repo.name}' on branch '{codebase.branch}': {str(e)}",
                    level=messages.ERROR
                )

        if success_count:
            self.message_user(
                request,
                f"Successfully pulled {success_count} codebase(s).",
                level=messages.SUCCESS
            )

    pull_codebases.short_description = "Pull selected codebases"


class TemplateArtifactForm(forms.ModelForm):
    class Meta:
        model = TemplateArtifact
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        source = getattr(self.instance, 'source', None)
        choices = []

        if source:
            try:
                repo_path = source.repo.get_local_path()
                source.update_codebase()  # Pull latest content

                for root, dirs, files in os.walk(repo_path):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]
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

@admin.register(TemplateArtifact)
class TemplateArtifactAdmin(admin.ModelAdmin):
    form = TemplateArtifactForm
    list_display = ('target', 'source', 'file_path')
    autocomplete_fields = ('target', 'source')
    search_fields = ('file_path', 'target__name', 'source__repo__name')
    actions = ['sync_templates']

    def sync_templates(self, request, queryset):
        success_count = 0
        failure_count = 0

        for template in queryset:
            try:
                template.sync_template()
                success_count += 1
            except Exception as e:
                failure_count += 1
                self.message_user(
                    request,
                    f"Failed to sync template '{template.file_path}': {str(e)}",
                    level=messages.ERROR
                )

        if success_count:
            self.message_user(
                request,
                f"Successfully synced {success_count} template(s).",
                level=messages.SUCCESS
            )

    sync_templates.short_description = "Sync selected templates"

# @admin.register(Image)
# class ImageAdmin(admin.ModelAdmin):
#     list_display = ('name', 'author', 'slug', 'created_at')
#     search_fields = ('name', 'slug', 'author__username')
#     readonly_fields = ('created_at', )
#     autocomplete_fields = ('author',)
