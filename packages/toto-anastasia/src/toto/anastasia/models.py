"""What the platform remembers about reserved compute.

Seven tables, and the reason each exists:

- ``ComputeLease`` — capacity a user consciously reserved. It is deducted from
  the pool **while idle**, because that is what a reservation means; a booking
  that only counted while busy would be a queue, not a reservation.
- ``GearRuntime`` — the mounted, alive half. Separate from the lease because
  mounting is a separate act: a user may hold capacity unmounted, and
  unmounting must not surrender the booking.
- ``Execution`` — one heavy job. Points at the caller's row by
  ``subject_label``/``subject_id`` STRINGS rather than a ForeignKey: a real FK
  to ``aralia.AraliaRun`` would make aralia a hard requirement of anastasia
  importing at all (``fields.E300`` on a host that installs one and not the
  other), and the same table has to serve texlab, ocr and dracena.
- ``GearEvent`` — the append-only history, refusals included.
- ``PoolGuard`` — one row, locked to serialise admission (see its docstring).
- ``AnastasiaUsageEvent`` / ``AnastasiaQuotaPolicy`` — the concrete pair every
  metered app must declare, because ``toto.quota`` owns no tables of its own.
  Without them ``policy_model_for("anastasia")`` returns None and the metric in
  ``metrics.py`` has nowhere to store a limit: the limits grid renders it as a
  row it cannot save, and ``toto.quota``'s own suite fails with "anastasia
  declares anastasia.execution but ships no quota table".

Where the truth lives: these rows are the DURABLE record (they survive
destroying every container). The manager's cgroups and containers are the
RUNTIME truth, and they are reconciled onto these rows, never the other way
round. Destroying all of Anastasia's runtime leaves every row here intact,
which is the campaign's final invariant.
"""

from __future__ import annotations

import uuid as uuid_module

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

from . import choices
from .limits import Limits


class LeaseQuerySet(models.QuerySet):
    def open(self, now=None):
        """Unreleased and unexpired — the only definition of "booked"."""
        now = now or timezone.now()
        return self.filter(released_at__isnull=True, expires_at__gt=now)

    def due_to_expire(self, now=None):
        now = now or timezone.now()
        return self.filter(released_at__isnull=True,
                           expires_at__lte=now or timezone.now())


class ComputeLease(models.Model):
    """Capacity a user reserved. A Compute Gear, before anyone mounts it."""

    #: The opaque identifier the manager knows this Gear by. A UUID rather than
    #: the pk because it travels to a process that must learn nothing about how
    #: many gears exist or who owns them.
    uuid = models.UUIDField(default=uuid_module.uuid4, unique=True,
                            editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="compute_leases")
    name = models.CharField(
        max_length=60,
        help_text="What this Gear is for — shown wherever you pick one.")

    cpu_millicores = models.PositiveIntegerField()
    ram_mb = models.PositiveIntegerField()
    scratch_mb = models.PositiveIntegerField()
    pids = models.PositiveIntegerField()

    #: Whether workspaces running in this Gear keep a persistent HOME.
    #:
    #: Asked when the Gear is RESERVED rather than when something is
    #: hibernated, because it changes what a runtime does from its first start:
    #: HOME moves off the throwaway tmpfs into the collected output area. A
    #: choice made later could not recover what the earlier kernels threw away.
    #:
    #: Off means hibernation keeps a manifest — packages, files, settings,
    #: position. On means it also keeps everything under $HOME: shell history,
    #: tool configuration, an interactive `pip --user` install.
    permanent_home = models.BooleanField(
        default=False,
        help_text="Keep each workspace's home directory between sessions "
                  "(shell history, tool configuration, interactive installs).")

    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    released_at = models.DateTimeField(null=True, blank=True)
    release_reason = models.CharField(max_length=200, blank=True)

    objects = LeaseQuerySet.as_manager()

    class Meta:
        verbose_name = "compute lease"
        ordering = ["-created_at", "-pk"]
        indexes = [
            # Explicit names throughout: an auto-generated name embeds a hash
            # that cannot be reproduced by hand, and the gate runs
            # `makemigrations --check --dry-run`.
            models.Index(fields=["released_at", "expires_at"],
                         name="anastasia_lease_open_idx"),
            models.Index(fields=["owner", "released_at"],
                         name="anastasia_lease_owner_idx"),
        ]
        constraints = [
            # Two live Gears called "thesis" is a person's mistake, not a
            # feature. Released ones may share a name freely.
            models.UniqueConstraint(
                fields=["owner", "name"], condition=Q(released_at__isnull=True),
                name="anastasia_one_live_name_per_owner"),
        ]

    def __str__(self):
        return f"{self.name} ({self.short_id})"

    @property
    def short_id(self) -> str:
        return str(self.uuid)[:8]

    @property
    def limits(self) -> Limits:
        return Limits(self.cpu_millicores, self.ram_mb, self.scratch_mb,
                      self.pids)

    def is_open(self, now=None) -> bool:
        now = now or timezone.now()
        return self.released_at is None and self.expires_at > now

    @property
    def slice_name(self) -> str:
        """The systemd slice this Gear's runners live in.

        Dash-nesting is systemd's own hierarchy notation: this slice sits
        inside ``anastasia.slice``, which carries the pool ceiling, so a
        runner is bounded by its Gear AND by the pool without either limit
        having to know about the other.
        """
        return f"anastasia-gear-{self.uuid.hex}.slice"


class PoolGuard(models.Model):
    """A single row whose only job is to serialise capacity admission.

    "Is there room in the pool" is a SUM over every open lease, and a sum has
    no row to lock. Two reservations evaluated concurrently would each read the
    same free capacity and each decide it fits — the classic over-booking race,
    and one that no unique constraint can express, because "the total must not
    exceed N" is not a property of any single row.

    So admission takes this row first. ``select_for_update`` is what serialises
    it on postgres; the ``touched`` bump is what serialises it on sqlite, where
    ``select_for_update`` is a no-op but a write inside the transaction still
    takes the database's write lock. Both backends therefore queue rather than
    race, without a second mechanism to keep in step.

    One row, ``pk=1``, created on demand. Nothing reads ``touched`` — it exists
    to be written.
    """

    touched = models.PositiveBigIntegerField(default=0)

    class Meta:
        verbose_name = "pool guard"

    def __str__(self):
        return "anastasia pool guard"


class GearRuntime(models.Model):
    """The mounted half: alive, bounded, and watched.

    One row per lease, reused across mount/unmount cycles rather than created
    afresh — the history of a Gear is one story, and a user who unmounts and
    remounts has not made a new Gear.
    """

    lease = models.OneToOneField(ComputeLease, on_delete=models.CASCADE,
                                 related_name="runtime")
    state = models.CharField(max_length=12, choices=choices.GEAR_STATES,
                             default=choices.UNMOUNTED)
    #: When the cached state was last written, so a page can say "I have not
    #: heard from the manager in 40 minutes" instead of quietly presenting a
    #: stale reading as current.
    state_at = models.DateTimeField(default=timezone.now)

    mounted_at = models.DateTimeField(null=True, blank=True)
    unmounted_at = models.DateTimeField(null=True, blank=True)

    #: Which manager generation mounted this. A manager restart mints a new
    #: one; a runtime still claiming the old generation after reconciliation
    #: could not be adopted and is DEAD.
    manager_generation = models.CharField(max_length=64, blank=True)

    #: The last reading: cpu_millicores_used, ram_mb_used, scratch_mb_used,
    #: pids_used, oom_kills, executions_running. Plain JSON — nothing here is
    #: aggregated or billed, it is what the page draws.
    last_sample = models.JSONField(default=dict, blank=True)
    sampled_at = models.DateTimeField(null=True, blank=True)

    #: Why the Gear is DEGRADED, in a sentence, when it is.
    detail = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "gear runtime"
        indexes = [
            models.Index(fields=["state"], name="anastasia_gear_state_idx"),
        ]

    def __str__(self):
        return f"{self.lease.name}: {self.state}"

    @property
    def is_mounted(self) -> bool:
        return self.state in choices.MOUNTED

    @property
    def accepts_work(self) -> bool:
        return self.state in choices.ACCEPTING

    def sample_age_seconds(self, now=None) -> float | None:
        if self.sampled_at is None:
            return None
        return ((now or timezone.now()) - self.sampled_at).total_seconds()


class ExecutionQuerySet(models.QuerySet):
    def live(self):
        return self.filter(status__in=(choices.PENDING, choices.RUNNING))

    def finished(self):
        return self.filter(status__in=tuple(choices.FINISHED))


class Execution(models.Model):
    """One heavy job: a disposable runner, created and destroyed."""

    uuid = models.UUIDField(default=uuid_module.uuid4, unique=True,
                            editable=False)
    lease = models.ForeignKey(ComputeLease, on_delete=models.CASCADE,
                              related_name="executions")

    operation = models.CharField(max_length=40)
    #: Denormalised from the operation so the index can answer "what is this
    #: Gear running" without a catalogue lookup per row.
    family = models.CharField(max_length=16)

    cpu_millicores = models.PositiveIntegerField()
    ram_mb = models.PositiveIntegerField()
    scratch_mb = models.PositiveIntegerField()
    pids = models.PositiveIntegerField()
    timeout_seconds = models.PositiveIntegerField()

    status = models.CharField(max_length=12, choices=choices.EXECUTION_STATUSES,
                              default=choices.PENDING)
    exit_code = models.IntegerField(null=True, blank=True)
    #: One sentence for a toast. The runner's own log is the caller's to keep.
    error = models.CharField(max_length=500, blank=True)

    #: Whose row this execution is for, WITHOUT an import edge — see the module
    #: docstring. ``subject_id`` is a CharField because not every caller's key
    #: is an integer and anastasia never does arithmetic on it.
    subject_label = models.CharField(max_length=100, blank=True)
    subject_id = models.CharField(max_length=64, blank=True)

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="anastasia_executions")

    #: What it actually cost: cpu_seconds, peak_ram_mb, scratch_peak_mb,
    #: oom_killed. Written once on completion, read by accounting.
    usage = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = ExecutionQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [
            models.Index(fields=["lease", "status"],
                         name="anastasia_exec_lease_idx"),
            models.Index(fields=["status", "created_at"],
                         name="anastasia_exec_status_idx"),
            models.Index(fields=["subject_label", "subject_id"],
                         name="anastasia_exec_subject_idx"),
        ]

    def __str__(self):
        return f"{self.operation} ({self.status})"

    @property
    def limits(self) -> Limits:
        return Limits(self.cpu_millicores, self.ram_mb, self.scratch_mb,
                      self.pids)

    @property
    def is_finished(self) -> bool:
        return self.status in choices.FINISHED


class GearEvent(models.Model):
    """One line of a Gear's history. Append-only, refusals included.

    A refused action is recorded rather than dropped: a history that only
    contains what worked cannot answer "why can I not mount this", which is the
    question it exists for.

    Honest about its limits: ``queryset.update()`` and raw SQL bypass both
    guards below. Append-only by discipline, not by hash chain.
    """

    RESERVE = "reserve"
    MOUNT = "mount"
    UNMOUNT = "unmount"
    RELEASE = "release"
    EXPIRE = "expire"
    EXECUTE = "execute"
    RECONCILE = "reconcile"
    DEGRADE = "degrade"

    KINDS = (
        (RESERVE, "Reserved"), (MOUNT, "Mounted"), (UNMOUNT, "Unmounted"),
        (RELEASE, "Released"), (EXPIRE, "Expired"), (EXECUTE, "Execution"),
        (RECONCILE, "Reconciled"), (DEGRADE, "Degraded"),
    )

    lease = models.ForeignKey(ComputeLease, on_delete=models.CASCADE,
                              related_name="events")
    kind = models.CharField(max_length=12, choices=KINDS)
    accepted = models.BooleanField(default=True)
    refusal_code = models.CharField(max_length=48, blank=True)
    from_state = models.CharField(max_length=12, blank=True)
    to_state = models.CharField(max_length=12, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="anastasia_events")
    detail = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        # By pk, not created_at: second-granularity ties make "the latest row"
        # ambiguous.
        ordering = ["pk"]
        indexes = [
            models.Index(fields=["lease", "kind"],
                         name="anastasia_event_lease_idx"),
        ]

    def __str__(self):
        arrow = "->" if self.accepted else "-x"
        return f"{self.kind} {arrow} {self.to_state or ''}".strip()

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "A gear event records something that already happened; it "
                "cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "The gear history is append-only; an event cannot be deleted.")


# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metric: anastasia.execution (one heavy job in a Gear) — a rate limit on
# submissions, NOT a price on compute. What compute costs is the reservation,
# which is a levy over time and belongs to toto.tax; see metrics.py.

class AnastasiaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Compute Gear usage event"
        verbose_name_plural = "Compute Gear usage events"


class AnastasiaQuotaPolicy(AbstractQuotaPolicy):
    events = AnastasiaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Compute Gear quota policy"
        verbose_name_plural = "Compute Gear quota policies"
