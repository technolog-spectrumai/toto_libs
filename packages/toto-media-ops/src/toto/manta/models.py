"""
Minimal job persistence for manta.

A job is a single, unconnected record: a ``name``, the ``celery_task_id`` (job
id), and a ``json`` ``output`` holding the full serialized result. No progress /
stdout / rendered argv, and no run history. ``MediaJob`` is a proxy subclass of
``FileJob`` for the ffmpeg/ffprobe commands.
"""

from django.contrib.auth.models import User
from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


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
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self):
        return self.name or f"{self.command} #{self.pk}"

    @property
    def is_terminal(self) -> bool:
        return self.status in (self.Status.DONE, self.Status.FAILED)

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    @property
    def output_file_ids(self) -> list:
        out = self.output if isinstance(self.output, dict) else {}
        return out.get("files", [])


def fail_job(job: FileJob, message: str) -> None:
    """Terminally close a job that will never report.

    FileJob has no error field by design — failures live in output['error']
    (the backends.py shape) — so the sweeper's reason lands there too.
    """
    from django.utils import timezone

    out = job.output if isinstance(job.output, dict) else {}
    out["error"] = message
    job.output = out
    job.status = FileJob.Status.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "output", "finished_at"])


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


# --------------------------------------------------------------------------- #
# Metering                                                                     #
# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.

class MantaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Manta usage event"
        verbose_name_plural = "Manta usage events"


class MantaQuotaPolicy(AbstractQuotaPolicy):
    events = MantaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Manta quota policy"
        verbose_name_plural = "Manta quota policies"
