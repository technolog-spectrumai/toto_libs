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
    #: The Compute Gear this job was submitted for, chosen on the form. Blank
    #: means "whichever one you hold", which only resolves while you hold
    #: exactly one — `require_gear` refuses to guess between two. Recorded
    #: here rather than resolved on the worker for the reason texlab records
    #: it on its run row: the choice is the submitter's and it is made now.
    gear_uuid = models.UUIDField(null=True, blank=True)
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
    (the backends.py shape) — so the sweeper's reason lands there too. A job
    closed while still PENDING never ran at all: the charge is refunded (the
    house rule — paid-for-but-never-delivered work refunds; work that RAN and
    failed keeps its charge).
    """
    from django.utils import timezone

    never_ran = job.status == FileJob.Status.PENDING
    out = job.output if isinstance(job.output, dict) else {}
    out["error"] = message
    job.output = out
    job.status = FileJob.Status.FAILED
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "output", "finished_at"])
    if never_ran:
        from toto.quota.charge import refund_for

        refund_for("manta.FileJob", job.pk, "manta.job",
                   reason=f"never ran: {message}")


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

class GearPreference(models.Model):
    """Which Compute Gear this person sends manta jobs to, remembered.

    Manta asked on every single form, and the answer is almost never different
    from last time — a person holds a Gear for days and runs a dozen conversions
    through it. `toto.ambrosia.gears` reached the same conclusion for the labs
    and made it a stored setting rather than a prompt; this is that setting for
    somebody who has no workspace to hang it on, so it hangs on the user.

    BLANK means Automatic — resolve the way manta always did, which works while
    you hold exactly one Gear and is refused by `require_gear` the moment you
    hold two. It is the default because it is the answer for everybody who has
    never thought about this, and it stays correct until they hold a second.

    A remembered choice is a CONVENIENCE, never an authorisation: every submit
    still runs the uuid through `jobs.require_gear`, so a Gear that was
    released, expired, or never belonged to this person is refused at the button
    exactly as a hand-typed one would be.
    """

    user = models.OneToOneField(
        User, on_delete=models.CASCADE, related_name="manta_gear_preference")
    gear_uuid = models.UUIDField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "gear preference"

    def __str__(self):
        return f"{self.user_id}: {self.gear_uuid or 'automatic'}"

    @classmethod
    def for_user(cls, user):
        """The remembered uuid as a string, or "" for Automatic.

        A string because that is what an HTML select round-trips; comparing a
        UUID object to an option value in a template silently never matches.
        """
        if not getattr(user, "is_authenticated", False):
            return ""
        row = cls.objects.filter(user=user).only("gear_uuid").first()
        return str(row.gear_uuid) if row and row.gear_uuid else ""

    @classmethod
    def remember(cls, user, gear_uuid) -> None:
        """Store what they just chose, Automatic included.

        Called after the submit has already validated the uuid, so this never
        records a Gear the person could not use.
        """
        if not getattr(user, "is_authenticated", False):
            return
        cls.objects.update_or_create(
            user=user, defaults={"gear_uuid": gear_uuid or None})


class MantaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Manta usage event"
        verbose_name_plural = "Manta usage events"


class MantaQuotaPolicy(AbstractQuotaPolicy):
    events = MantaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Manta quota policy"
        verbose_name_plural = "Manta quota policies"
