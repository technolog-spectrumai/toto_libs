"""What the platform remembers about reserved compute.

Seven tables, and the reason each exists:

- ``ComputeLease`` — capacity a user consciously reserved. It is deducted from
  the pool **while idle**, because that is what a reservation means; a booking
  that only counted while busy would be a queue, not a reservation.
- ``CapsuleRuntime`` — the mounted, alive half. Separate from the lease because
  mounting is a separate act: a user may hold capacity unmounted, and
  unmounting must not surrender the booking.
- ``Execution`` — one heavy job. Points at the caller's row by
  ``subject_label``/``subject_id`` STRINGS rather than a ForeignKey: a real FK
  to ``aralia.AraliaRun`` would make aralia a hard requirement of anastasia
  importing at all (``fields.E300`` on a host that installs one and not the
  other), and the same table has to serve texlab, ocr and dracena.
- ``CapsuleEvent`` — the append-only history, refusals included.
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
    """Capacity a user reserved. A Compute Capsule, before anyone mounts it."""

    #: The opaque identifier the manager knows this Capsule by. A UUID rather than
    #: the pk because it travels to a process that must learn nothing about how
    #: many capsules exist or who owns them.
    uuid = models.UUIDField(default=uuid_module.uuid4, unique=True,
                            editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="compute_leases")
    name = models.CharField(
        max_length=60,
        help_text="What this Capsule is for — shown wherever you pick one.")

    cpu_millicores = models.PositiveIntegerField()
    ram_mb = models.PositiveIntegerField()
    scratch_mb = models.PositiveIntegerField()
    pids = models.PositiveIntegerField()

    #: Whether workspaces running in this Capsule keep a persistent HOME.
    #:
    #: Asked when the Capsule is RESERVED rather than when something is
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

    #: Whether this Capsule's jobs may reach the internet, through the proxy.
    #:
    #: ASKED AT RESERVATION, like `permanent_home` above and for a related
    #: reason: it is a property of the capacity somebody booked, not of the job
    #: they happen to run. A family declaring egress would hand it to every
    #: user of that family on every host; the reservation is where one person
    #: accepted one trade, so the reservation is where it is recorded. (Two
    #: egress postures previously lived on `Family` and were deleted on
    #: 2026-09-10 — see the note there for how their ordering misrouted a job.)
    #:
    #: OFF BY DEFAULT, and every Capsule reserved before this field existed is
    #: off: a migration defaulting to True would give the internet to capacity
    #: whose owner never asked for it and is not around to be asked.
    #:
    #: WHAT IT IS NOT. Not "unrestricted internet". A Capsule with this set
    #: gets ONE NIC onto a proxy network, where the executor's nftables table
    #: makes the proxy's address and port the only reachable thing, and the
    #: proxy refuses every destination not on the host's allowlist. It also
    #: depends on the HOST offering egress: a mount asking for it where the
    #: packet filter is not in the kernel is REFUSED, never quietly downgraded.
    egress = models.BooleanField(
        default=False,
        help_text="Let jobs in this Capsule reach the internet, through this "
                  "platform's filtering proxy. Only the destinations your "
                  "administrator allows are reachable.")

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
            # Two live Capsules called "thesis" is a person's mistake, not a
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
        """The systemd slice this Capsule's runners live in.

        Dash-nesting is systemd's own hierarchy notation: this slice sits
        inside ``anastasia.slice``, which carries the pool ceiling, so a
        runner is bounded by its Capsule AND by the pool without either limit
        having to know about the other.
        """
        return f"anastasia-capsule-{self.uuid.hex}.slice"


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


class CapsuleRuntime(models.Model):
    """The mounted half: alive, bounded, and watched.

    One row per lease, reused across mount/unmount cycles rather than created
    afresh — the history of a Capsule is one story, and a user who unmounts and
    remounts has not made a new Capsule.
    """

    lease = models.OneToOneField(ComputeLease, on_delete=models.CASCADE,
                                 related_name="runtime")
    state = models.CharField(max_length=12, choices=choices.CAPSULE_STATES,
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

    #: WHICH ISOLATION this Capsule is mounted under, as the executor reported it
    #: at mount time — never as a setting claimed.
    #:
    #: The distinction is the whole reason the column exists. A setting says
    #: what an operator asked for; this says what the runtime answered, and
    #: they disagree exactly when it matters: a host configured for VMs whose
    #: Kata runtime is not registered, a Capsule mounted before a tier change and
    #: still running under the old one.
    #:
    #: Blank means "mounted before this column existed, or by an executor too
    #: old to say" — which the page must read as UNKNOWN and therefore as the
    #: weakest claim, never as the strongest.
    tier = models.CharField(max_length=32, blank=True)

    #: The last reading: cpu_millicores_used, ram_mb_used, scratch_mb_used,
    #: pids_used, oom_kills, executions_running. Plain JSON — nothing here is
    #: aggregated or billed, it is what the page draws.
    last_sample = models.JSONField(default=dict, blank=True)
    sampled_at = models.DateTimeField(null=True, blank=True)

    #: Why the Capsule is DEGRADED, in a sentence, when it is.
    detail = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "capsule runtime"
        indexes = [
            models.Index(fields=["state"], name="anastasia_capsule_state_idx"),
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
    #: Capsule running" without a catalogue lookup per row.
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
    def short_id(self) -> str:
        """The first eight characters of the uuid, for a person.

        Operators read job ids off a screen and type them into a grep; a full
        uuid in a table column is unreadable and gets truncated by the browser
        at an arbitrary point, which is worse than truncating it deliberately.
        """
        return str(self.uuid)[:8]

    @property
    def is_finished(self) -> bool:
        return self.status in choices.FINISHED


class CapsuleEvent(models.Model):
    """One line of a Capsule's history. Append-only, refusals included.

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
                "A capsule event records something that already happened; it "
                "cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "The capsule history is append-only; an event cannot be deleted.")


class InstallRun(models.Model):
    """One request to install packages into a Capsule, watched rather than
    waited on.

    THE INSTALL PATH (todo 9.4). A `jobs.run` is submit-wait-collect inside one
    request; an install is minutes of pip output that somebody wants to WATCH,
    so it is a row that is advanced by whoever looks at it — the API poll, the
    beat — rather than a call that blocks until it is over.

    A PHASE AND A COUNT, NOT ONE PERCENTAGE. sepulka's position, kept here on
    purpose: resolving, downloading and installing are not comparable stages,
    and one bar drawn over all of them "would be a lie told smoothly". The
    count is honest in a narrower way too — it is how many of the REQUESTED
    distributions pip has reported, not how many of the dependencies it
    decided to fetch, because the former is known in advance and the latter is
    not known until resolution ends.

    THE LOG IS A COPY. The runner's own output streams through
    `execution_logs` byte-offset by byte-offset; this row keeps what has been
    read so far, appended in SQL, so the record survives the runner (which is
    thrown away) and so a client that arrives late reads the whole thing. It
    is capped — a log is a diagnostic, not an archive — and says when it was.

    WHERE THE PACKAGES GO. `/files/site-packages`, the Capsule's files area
    (todo 9.7), which lives as long as the reservation: an install survives
    every later job and every unmount, and dies with the Capsule. Nothing is
    installed into the runner image, which stays read-only.
    """

    uuid = models.UUIDField(default=uuid_module.uuid4, unique=True,
                            editable=False)
    lease = models.ForeignKey(ComputeLease, on_delete=models.CASCADE,
                              related_name="installs")
    #: The job that carries it. SET_NULL because an execution row may be
    #: swept independently; the install keeps its record either way.
    execution = models.ForeignKey(Execution, on_delete=models.SET_NULL,
                                  null=True, blank=True,
                                  related_name="installs")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True,
        blank=True, related_name="anastasia_installs")

    #: WHAT is being installed: Python distributions into
    #: /files/site-packages, or CTAN packages into /files/texmf. One row type
    #: for both, because what a person watches — a phase, a count, a log — is
    #: the same, and `install.KINDS` is where the two differ.
    KIND_CHOICES = (("python", "Python"), ("latex", "LaTeX"))
    kind = models.CharField(max_length=12, choices=KIND_CHOICES,
                            default="python")

    #: The requested names, as cleaned by the operation's parameter:
    #: PEP 503-normalised for Python, CTAN ids for LaTeX.
    packages = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=12, choices=choices.EXECUTION_STATUSES,
                              default=choices.PENDING)
    #: queued, resolving, downloading, installing, finished, failed. Derived
    #: from the log by `install.refresh`, never typed by a caller.
    phase = models.CharField(max_length=32, blank=True)
    packages_done = models.PositiveIntegerField(default=0)
    packages_total = models.PositiveIntegerField(default=0)

    #: The appendable copy of the runner's output. See the class docstring.
    log = models.TextField(blank=True)
    #: BYTE offset into the runner's log, so the next read starts where the
    #: last one stopped. Bytes, not characters: the executor slices bytes.
    log_offset = models.PositiveIntegerField(default=0)
    #: Set when the copy stopped growing at its cap. The runner's log is still
    #: readable through `execution_logs` while the runner exists.
    log_truncated = models.BooleanField(default=False)

    #: One sentence for a person: why it failed, or what to look at.
    detail = models.CharField(max_length=500, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["lease", "status"],
                                name="anastasia_install_lease_idx")]

    def __str__(self):
        return f"install {'+'.join(self.packages)} ({self.status})"

    @property
    def is_finished(self) -> bool:
        return self.status in choices.FINISHED


# --------------------------------------------------------------------------- #
# toto.quota owns no tables, so each metered app declares its own concrete pair
# and the rows live in that app's migrations. See toto/quota/models.py.
# Metric: anastasia.execution (one heavy job in a Capsule) — a rate limit on
# submissions, NOT a price on compute. What compute costs is the reservation,
# which is a levy over time and belongs to toto.tax; see metrics.py.

class AnastasiaUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Compute Capsule usage event"
        verbose_name_plural = "Compute Capsule usage events"


class AnastasiaQuotaPolicy(AbstractQuotaPolicy):
    events = AnastasiaUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Compute Capsule quota policy"
        verbose_name_plural = "Compute Capsule quota policies"


# The API token lives in its own module because it is about ACCESS, not about
# compute — but Django only discovers models imported from `models`, so the
# import is here and deliberate rather than accidental.
from .tokens import CapsuleToken  # noqa: E402,F401  (re-export for migrations)

# Retained history, in its own module because it is about TIME rather than
# about a capsule's current state. Imported here so Django discovers it.
from .samples import CapsuleSample  # noqa: E402,F401  (re-export for migrations)
