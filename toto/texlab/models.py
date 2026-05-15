from django.db import models
from django.utils.text import slugify
from toto.vault.models import Bucket, VaultFile


class LatexWorkspace(models.Model):
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)

    bucket = models.ForeignKey(
        Bucket,
        on_delete=models.CASCADE,
        related_name="latex_workspaces"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("bucket", "slug")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    @property
    def owner(self):
        return self.bucket.owner

    def __str__(self):
        return f"{self.name} ({self.owner.username})"


class LatexFile(models.Model):
    FILE_TYPES = [
        ("tex", "TeX source"),
        ("sty", "Style file"),
        ("bib", "BibTeX database"),
        ("pdf", "Compiled PDF"),
        ("other", "Other text"),
    ]

    workspace = models.ForeignKey(
        LatexWorkspace,
        on_delete=models.CASCADE,
        related_name="files"
    )

    vault_file = models.ForeignKey(
        VaultFile,
        on_delete=models.CASCADE,
        related_name="texlab_entries"
    )

    file_type = models.CharField(max_length=10, choices=FILE_TYPES, default="tex")

    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("workspace", "vault_file")

    def __str__(self):
        return f"{self.vault_file.title} in {self.workspace.name}"
