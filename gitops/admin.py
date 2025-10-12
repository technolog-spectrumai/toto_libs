from django.contrib import admin
from .models import GitRepository, Artifact
from .batch import BatchAction
from django import forms
from django.core.files.base import ContentFile


@admin.register(GitRepository)
class GitRepositoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'provider', 'linked_at')
    search_fields = ('name', 'url', 'provider')
    actions = ['pull_repositories']

    def pull_repositories(self, request, queryset):
        def operation(repo):
            result = repo.pull()
            return repo
        result = BatchAction(queryset).run(operation)
        BatchAction.display_messages(result, self.message_user, request, verb="pull")

    pull_repositories.short_description = "Pull selected repositories"

# Optional: Replace with your preferred widget or use a basic Textarea
class ArtifactAdminForm(forms.ModelForm):
    file_content = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 20, 'cols': 100}),
        required=False,
        label="File Content"
    )

    class Meta:
        model = Artifact
        fields = ('repository', 'branch', 'file_path', 'file_blob')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        file_field = self.instance.file_blob
        if file_field and hasattr(file_field, 'read'):
            try:
                self.fields['file_content'].initial = file_field.read().decode('utf-8')
            except Exception:
                self.fields['file_content'].initial = ''

    def save(self, commit=True):
        instance = super().save(commit=False)
        content = self.cleaned_data.get('file_content', '')
        if content:
            filename = instance.file_path.replace('/', '_') or 'artifact.html'
            instance.file_blob.save(filename, ContentFile(content), save=False)
        if commit:
            instance.save()
        return instance

@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    form = ArtifactAdminForm
    list_display = ('repository', 'branch', 'file_path')
    search_fields = ('branch', 'file_path')
    list_filter = ('branch', 'repository')