"""
Minimal job persistence for manta.

A job is a single, unconnected record: a ``name``, the ``celery_task_id`` (job
id), and a ``json`` ``output`` holding the full serialized result. No progress /
stdout / rendered argv, and no run history. ``MediaJob`` is a proxy subclass of
``FileJob`` for the ffmpeg/ffprobe commands; transcribe is a plain ``FileJob``
row whose output is filled by the FileServiceRun backend.
"""

from django.contrib.auth.models import User
from django.db import models


class FileJob(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    name = models.CharField(max_length=200, blank=True)
    command = models.CharField(max_length=50)                 # command key, e.g. "compress"
    owner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    celery_task_id = models.CharField(max_length=100, blank=True)  # the job id
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.PENDING)
    inputs = models.JSONField(default=list)                   # input VaultFile ids
    params = models.JSONField(default=dict)
    output = models.JSONField(default=dict)                   # full serialized output
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name or f"{self.command} #{self.pk}"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.Status.DONE, self.Status.FAILED)

    @property
    def output_file_ids(self) -> list:
        out = self.output if isinstance(self.output, dict) else {}
        return out.get("files", [])


class MediaJob(FileJob):
    """Proxy of FileJob for the ffmpeg/ffprobe commands."""

    class Meta:
        proxy = True

    @property
    def primary_input(self):
        ids = self.inputs or []
        return ids[0] if ids else None

    @property
    def extra_inputs(self) -> list:
        ids = self.inputs or []
        return ids[1:]
