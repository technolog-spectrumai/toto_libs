from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Workspace(models.Model):
    name        = models.CharField(max_length=200)
    slug        = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    bucket      = models.ForeignKey("vault.Bucket", on_delete=models.CASCADE, related_name="videomant_workspaces")
    owner       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="videomant_workspaces")
    allowed_users = models.ManyToManyField(User, blank=True, related_name="shared_videomant_workspaces")
    created_at  = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Workspace"
        verbose_name_plural = "Workspaces"

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name) or "workspace"
            slug, n = base, 1
            while Workspace.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"; n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    def user_has_access(self, user):
        return user == self.owner or self.allowed_users.filter(pk=user.pk).exists()

    def __str__(self):
        return f"{self.name} ({self.owner.username})"


class MediaJob(models.Model):
    class Status(models.TextChoices):
        PENDING   = "pending",   "Pending"
        RUNNING   = "running",   "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED    = "failed",    "Failed"
        CANCELED  = "canceled",  "Canceled"

    owner          = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs")
    workspace      = models.ForeignKey(Workspace, on_delete=models.SET_NULL, null=True, blank=True, related_name="jobs")
    task_name      = models.CharField(max_length=100)
    workflow_run   = models.ForeignKey("workflows.WorkflowRun",     on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs")
    workflow_node_run = models.ForeignKey("workflows.WorkflowNodeRun", on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs")
    input_file     = models.ForeignKey("vault.VaultFile", on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs_as_input")
    secondary_file = models.ForeignKey("vault.VaultFile", on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs_as_secondary")
    input_files    = models.JSONField(default=list, blank=True, help_text="List of VaultFile IDs for concat")
    params         = models.JSONField(default=dict, blank=True)
    rendered_argv  = models.JSONField(default=list, blank=True)
    output_file    = models.ForeignKey("vault.VaultFile", on_delete=models.SET_NULL, null=True, blank=True, related_name="media_jobs_as_output")
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    progress_percent = models.PositiveSmallIntegerField(default=0)
    progress_message = models.CharField(max_length=255, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    stdout         = models.TextField(blank=True)
    stderr         = models.TextField(blank=True)
    exit_code      = models.IntegerField(null=True, blank=True)
    error_message  = models.TextField(blank=True)
    output_metadata = models.JSONField(default=dict, blank=True)
    started_at     = models.DateTimeField(null=True, blank=True)
    finished_at    = models.DateTimeField(null=True, blank=True)
    created_at     = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Media Job"
        verbose_name_plural = "Media Jobs"

    def __str__(self):
        return f"{self.task_name} #{self.id} [{self.status}]"

    @property
    def is_terminal(self):
        return self.status in (self.Status.SUCCEEDED, self.Status.FAILED, self.Status.CANCELED)

    @property
    def is_running(self):
        return self.status in (self.Status.PENDING, self.Status.RUNNING)


class ProbeResult(models.Model):
    vault_file    = models.ForeignKey("vault.VaultFile",  on_delete=models.CASCADE, related_name="probe_results")
    job           = models.ForeignKey(MediaJob, on_delete=models.SET_NULL, null=True, blank=True, related_name="probe_results")
    raw           = models.JSONField(default=dict, blank=True)
    format        = models.JSONField(default=dict, blank=True)
    streams       = models.JSONField(default=list, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    width         = models.PositiveIntegerField(null=True, blank=True)
    height        = models.PositiveIntegerField(null=True, blank=True)
    video_codec   = models.CharField(max_length=50, blank=True)
    audio_codec   = models.CharField(max_length=50, blank=True)
    created_at    = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Probe Result"
        verbose_name_plural = "Probe Results"

    def __str__(self):
        return f"ProbeResult for VaultFile#{self.vault_file_id}"
