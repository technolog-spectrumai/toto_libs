from django.db import models
from git import Repo, GitCommandError, InvalidGitRepositoryError
import os
from django.conf import settings


class GitRepository(models.Model):
    name = models.CharField(max_length=255, help_text="Name of the Git repository")
    url = models.URLField(help_text="URL of the Git repository")
    access_key = models.TextField(help_text="Access key or token for authentication")
    provider = models.CharField(max_length=100, help_text="Git hosting provider (e.g., GitHub, GitLab, Bitbucket)")
    linked_at = models.DateTimeField(auto_now_add=True, help_text="Timestamp when the repository was linked")

    def __str__(self):
        return self.name

    def pull(self):
        local_path = os.path.join(settings.GIT_REPO_BASE_DIR, self.name.replace(' ', '_'))
        os.makedirs(local_path, exist_ok=True)

        try:
            repo = Repo(local_path)
        except InvalidGitRepositoryError:
            # Not a valid repo — try cloning
            try:
                repo = Repo.clone_from(self.url, local_path)
            except GitCommandError as e:
                raise RuntimeError(f"Failed to clone '{self.name}': {e}")

        try:
            origin = repo.remotes.origin
            pull_result = origin.pull()
            if not pull_result:
                raise RuntimeError("No changes pulled or pull failed silently.")
            return f"Pulled successfully: {[str(ref) for ref in pull_result]}"
        except GitCommandError as e:
            raise RuntimeError(f"Git error for '{self.name}': {e}")
        except Exception as e:
            raise RuntimeError(f"Unexpected error for '{self.name}': {e}")

class Artifact(models.Model):
    repository = models.ForeignKey(GitRepository, on_delete=models.CASCADE, related_name='artifacts')
    branch = models.CharField(max_length=255, help_text="Branch where the file is located")
    file_path = models.CharField(max_length=1024, help_text="Path to the file in the repository")

    def __str__(self):
        return f"{self.repository.name}:{self.branch}/{self.file_path}"
