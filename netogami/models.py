from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify
import uuid
from django.urls import reverse
import git
import os
import tempfile
import shutil
import uuid
from urllib.parse import urlparse
from encrypted_model_fields.fields import EncryptedCharField


class Template(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, null=True)
    content = models.TextField(
        help_text="Full Django template content including <head> and body with {{ variables }}"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class Page(models.Model):
    template = models.ForeignKey(Template, on_delete=models.CASCADE, related_name='pages')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='pages')
    data = models.JSONField(help_text="Context for rendering the template")
    language = models.CharField(max_length=20, default='en')
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(f"{self.template.name}-{uuid.uuid4()}")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.slug} by {self.author.username}"

    def get_url(self):
        return reverse('page_detail', kwargs={'slug': self.slug, 'language': self.language})


class TemplateSource(models.Model):
    name = models.CharField(max_length=100, unique=True)
    repo_url = models.URLField()
    branch = models.CharField(max_length=100, default='main')
    token = EncryptedCharField(max_length=100, default='main')

    def __str__(self):
        return f"{self.name} [{self.branch}]"


    def get_local_path(self):
        from django.conf import settings
        base_dir = os.path.join(settings.BASE_DIR, 'template_repos')
        os.makedirs(base_dir, exist_ok=True)
        return os.path.join(base_dir, self.name)

    def _get_secure_url(self):
        parsed = urlparse(self.repo_url)
        return f"https://{self.token}@{parsed.hostname}{parsed.path}"

    def _checkout(self, repo_path):
        repo = git.Repo(repo_path)
        repo.git.checkout(self.branch)
        repo.remotes.origin.pull()
        return repo

    def _clone(self, repo_path):
        return git.Repo.clone_from(self._get_secure_url(), repo_path, branch=self.branch)

    def pull_repo(self):
        repo_path = self.get_local_path()
        try:
            if os.path.exists(repo_path):
                repo = self._checkout(repo_path)
            else:
                repo = self._clone(repo_path)
        except Exception as e:
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Git pull failed: {e}")
        return repo


class TemplateGenerator(models.Model):
    source = models.ForeignKey(TemplateSource, on_delete=models.CASCADE, related_name='generators')
    target = models.ForeignKey(Template, on_delete=models.CASCADE, related_name='generators')
    file_path = models.CharField(max_length=255, blank=True, help_text="Relative path to the template file in the repo")
    last_synced = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.target.name} ← {self.file_path} in {self.source.name}"

    def get_full_file_path(self):
        return os.path.join(self.source.get_local_path(), self.file_path)

    def sync_template(self):
        """
        Pulls the latest repo and updates the target Template content from the file.
        """
        repo = self.source.pull_repo()
        file_full_path = self.get_full_file_path()

        if not os.path.exists(file_full_path):
            raise FileNotFoundError(f"Template file not found: {file_full_path}")

        with open(file_full_path, 'r', encoding='utf-8') as f:
            self.target.content = f.read()
            self.target.save()

        self.save()
        return self.target


# class Image(models.Model):
#     author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='images')
#     name = models.CharField(max_length=100)
#     slug = models.SlugField(max_length=255, unique=True, blank=True)
#     image = models.ImageField(upload_to='uploads/images/')
#     created_at = models.DateTimeField(auto_now_add=True)
#
#     def save(self, *args, **kwargs):
#         if not self.slug:
#             base = slugify(self.name)
#             self.slug = f"{base}-{uuid.uuid4().hex[:6]}"
#         super().save(*args, **kwargs)
#
#     def __str__(self):
#         return self.name


