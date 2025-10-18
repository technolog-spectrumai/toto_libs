from django.db import models
from git import Repo, GitCommandError, InvalidGitRepositoryError
import os
from django.conf import settings
from django.core.files.base import ContentFile
import shutil
from vault.models import Bucket, VaultFile
from django.utils.text import slugify


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
        finally:
            self.bind_artifacts(repo)
            repo.close()

    def _send_artifact_to_bucket(self, repo, artifact):
        # Checkout the correct branch
        repo.git.checkout(artifact.branch)

        # Construct full path to the file
        full_path = os.path.join(repo.working_tree_dir, artifact.file_path)

        # Read file content
        with open(full_path, 'rb') as f:
            content = f.read()
            filename = artifact.file_path.split('/')[-1] or 'artifact.txt'

            # Ensure bucket exists
            if not artifact.bucket:
                raise RuntimeError(f"Artifact '{artifact.file_path}' has no bucket assigned.")

            ext = filename.lower()
            if ext.endswith('.pdf'):
                file_type = 'pdf'
            elif ext.endswith(('.png', '.jpg', '.jpeg')):
                file_type = 'image'
            elif ext.endswith(('.txt', '.md', '.tex')):
                file_type = 'text'
            elif ext.endswith(('.html', '.htm', '.xml')):
                file_type = 'html'
            else:
                file_type = 'text'

            # Create VaultFile in the bucket
            VaultFile.objects.create(
                owner=artifact.bucket.owner,
                title=f"{self.name}: {filename}",
                file=ContentFile(content, name=filename),
                file_type=file_type,
                bucket=artifact.bucket,
                notes=f"Pulled from {self.name} on branch {artifact.branch}"
            )

    def bind_artifacts(self, repo):
        for artifact in self.artifacts.all():
            try:
                self._send_artifact_to_bucket(repo, artifact)
            except FileNotFoundError:
                raise RuntimeError(f"❌ File not found: {artifact.file_path} in branch {artifact.branch}")
            except Exception as e:
                raise RuntimeError(f"⚠️ Error processing file '{artifact.file_path}': {e}")

    def post_delete(self):
        local_path = os.path.join(settings.GIT_REPO_BASE_DIR, self.name.replace(' ', '_'))
        if os.path.isdir(local_path):
            try:
                shutil.rmtree(local_path)
            except Exception as e:
                raise RuntimeError(f"Failed to delete local repo directory: {e}")


class Artifact(models.Model):
    repository = models.ForeignKey(GitRepository, on_delete=models.CASCADE, related_name='artifacts')
    branch = models.CharField(max_length=255, help_text="Branch where the file is located")
    file_path = models.CharField(max_length=1024, help_text="Path to the file in the repository")
    bucket = models.ForeignKey(Bucket, on_delete=models.SET_NULL, null=True, blank=True, related_name='artifacts',
                               help_text="Bucket where the artifact is stored")

    def __str__(self):
        return f"{self.repository.name}:{self.branch}/{self.file_path}"

