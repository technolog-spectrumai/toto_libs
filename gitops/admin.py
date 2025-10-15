from django.contrib import admin
from .models import GitRepository, Artifact
from .batch import BatchAction


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


@admin.register(Artifact)
class ArtifactAdmin(admin.ModelAdmin):
    list_display = ('repository', 'branch', 'file_path')
    search_fields = ('branch', 'file_path')
    list_filter = ('branch', 'repository')