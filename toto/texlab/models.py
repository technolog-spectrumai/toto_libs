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


class CompileRun(models.Model):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED  = "failed"

    STATUS_CHOICES = [
        (PENDING, "Pending"),
        (RUNNING, "Running"),
        (SUCCESS, "Success"),
        (FAILED,  "Failed"),
    ]

    workspace  = models.ForeignKey(
        LatexWorkspace,
        on_delete=models.CASCADE,
        related_name="compile_runs",
    )
    latex_file = models.ForeignKey(
        LatexFile,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="compile_runs",
    )
    task_id    = models.CharField(max_length=255, blank=True)
    status     = models.CharField(max_length=20, choices=STATUS_CHOICES, default=PENDING)
    log        = models.TextField(blank=True)
    pdf_url    = models.CharField(max_length=500, blank=True)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"CompileRun {self.id} [{self.status}] – {self.workspace.name}"
