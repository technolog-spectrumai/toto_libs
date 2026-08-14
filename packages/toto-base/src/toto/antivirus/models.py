"""What the antivirus remembers.

Two rows and nothing else: what a scan concluded, and what each person wants
scanned automatically.

**A finding never changes the file.** The bytes stay exactly as they were and
stay downloadable. A scanner is a piece of software with opinions, and the cost
of a false positive must not be somebody losing their work. Containment is a
later decision, made deliberately, not a side effect of shipping a scanner.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent
from toto.vault.scanning import SCANNABLE_TYPES

#: What an on-demand scan costs. One code: the price of a scan does not vary
#: with the file — the scanner is a bounded pass over bounded bytes.
METRIC_SCAN = "antivirus.scan"


#: The automatic doors, grouped as a person configures them. Keys are what
#: ``ScanPreference.doors`` stores; values are the door strings the call sites
#: actually record on ScanResult rows. "manual" is deliberately absent —
#: pressing Scan is already the deliberate act preferences exist to replace.
DOOR_GROUPS = {
    "editor": ("editor", "socket"),
    "upload": ("api-upload", "api", "gateway"),
    "restore": ("restore",),
}
DOOR_GROUPS_BY_DOOR = {door: group
                       for group, doors in DOOR_GROUPS.items()
                       for door in doors}


class ScanVerdict(models.TextChoices):
    CLEAN = "clean", "Clean"
    REFUSED = "refused", "Refused"
    #: The scan itself failed — unreadable bytes, a storage error. Recorded
    #: rather than swallowed: a file that CANNOT be checked is a security fact
    #: of its own kind, and before this verdict existed the panel forgot the
    #: failure the moment the 400 response was gone.
    ERROR = "error", "Could not scan"


#: Refusal reasons that mean the content tried to DO something — run script,
#: reach out of the document — as opposed to merely being the wrong shape.
#: The scanners' own vocabulary (scanners/markup.py, scanners/json_scan.py).
_ACTIVE_REASONS = frozenset({"active-content", "external-reference"})

#: What a Pathology row calls itself. The scanner refuses, it does not grade —
#: so severity is DERIVED from the refusal reason, in one place, rather than
#: each template inventing its own reading of "malformed".
SEVERITY_THREAT = "threat"          # active content: script, outbound refs
SEVERITY_SUSPICIOUS = "suspicious"  # structural: malformed, wrong shape, depth
SEVERITY_FAILED = "failed"          # the scan itself could not run


def severity_of(verdict: str, reason: str = "") -> str:
    """One finding's severity, from what the scanner actually said."""
    if verdict == ScanVerdict.ERROR:
        return SEVERITY_FAILED
    if reason in _ACTIVE_REASONS:
        return SEVERITY_THREAT
    return SEVERITY_SUSPICIOUS


class ScanResult(models.Model):
    """One scan of one file's contents, at one moment.

    Keyed on **the hash, not the file**: a verdict is about bytes. Editing a
    file does not make its old verdict wrong, it makes it about something else —
    which is exactly why the clean tick disappears the moment content changes
    rather than lingering on a file nobody has re-checked.
    """

    file = models.ForeignKey(
        "vault.VaultFile", on_delete=models.CASCADE, related_name="scan_results")
    content_sha256 = models.CharField(
        max_length=64, db_index=True,
        help_text="The bytes this verdict is about. Not necessarily current.")
    file_type = models.CharField(max_length=16)

    verdict = models.CharField(max_length=16, choices=ScanVerdict.choices)
    reason = models.CharField(max_length=64, blank=True)
    detail = models.TextField(blank=True)
    line = models.PositiveIntegerField(default=0)

    #: Which entrance this came through — "editor", "upload", "gateway",
    #: "websocket", "restore", "manual". Worth keeping: it is how you find out
    #: which door is letting things in.
    door = models.CharField(max_length=32, blank=True)
    #: How many bytes were actually read for this scan. What makes "data
    #: scanned" on the Statistics tab an honest number: the file's CURRENT size
    #: is a different quantity the moment anybody edits it.
    size_bytes = models.PositiveBigIntegerField(default=0)
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="+")
    scanned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-scanned_at"]
        verbose_name = "scan result"
        constraints = [
            # One verdict per (file, bytes). Re-scanning unchanged content
            # updates rather than piling up rows.
            models.UniqueConstraint(fields=["file", "content_sha256"],
                                    name="antivirus_one_verdict_per_content"),
        ]
        indexes = [
            # Serves the threats page and the tick lookup respectively.
            models.Index(fields=["verdict", "-scanned_at"]),
            models.Index(fields=["file", "content_sha256"]),
        ]

    def __str__(self):
        return f"{self.file_id} {self.verdict}"

    @property
    def is_threat(self) -> bool:
        return self.verdict == ScanVerdict.REFUSED

    @property
    def severity(self) -> str:
        return severity_of(self.verdict, self.reason)


class ScanPreference(models.Model):
    """Which file types this person wants screened automatically.

    Per user, and it can only ever narrow **their own** files — nobody can
    weaken screening for anybody else. It governs the automatic doors only: the
    Scan button always works, because pressing it is already a deliberate act.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="scan_preference")
    #: Absent from this list means "do not scan my files of that type on the
    #: way in". Defaults to everything.
    types = models.JSONField(default=list, blank=True)
    #: Which automatic DOORS screen this person's files, as group names from
    #: :data:`DOOR_GROUPS`. ``None`` — never chosen — means every door; a saved
    #: list is exact. The asymmetry with ``types`` is deliberate backward
    #: compatibility: rows created before this field exist with no list, and
    #: an empty default list would have silently switched every door off.
    doors = models.JSONField(null=True, blank=True, default=None)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "scan preference"

    def __str__(self):
        return f"{self.user_id}: {', '.join(self.types) or 'nothing'}"

    @classmethod
    def applies(cls, user, file_type: str, door: str = "") -> bool:
        """Whether this owner's preference lets a door screen this file.

        The rule unchanged from day one: a preference can only NARROW your own
        files. No row, an unknown door, or no signed-in owner all mean SCAN —
        scanning more is always the safe failure direction.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return file_type in SCANNABLE_TYPES
        if file_type not in cls.types_for(user):
            return False
        row = cls.objects.filter(user=user).first()
        if row is None or row.doors is None:
            return True
        group = DOOR_GROUPS_BY_DOOR.get(door)
        if group is None:
            # A door this vocabulary does not know is never silently exempt.
            return True
        return group in row.doors

    @classmethod
    def types_for(cls, user) -> tuple[str, ...]:
        """What to scan for this user. Everything, unless they said otherwise."""
        if user is None or not getattr(user, "is_authenticated", False):
            return SCANNABLE_TYPES
        row = cls.objects.filter(user=user).first()
        if row is None:
            return SCANNABLE_TYPES
        return tuple(t for t in row.types if t in SCANNABLE_TYPES)


class RunStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Done"
    FAILED = "failed", "Failed"


class ScanRun(models.Model):
    """One on-demand scan, queued on a worker.

    The DOOR scans stay inline — a refusal there decides whether a save
    happens, so it cannot be deferred. The Scan button is different: it is
    ordered work, it bills, and a browser waiting on a synchronous request is
    a spinner that is really a blocked socket. Same run shape as every queued
    job here (texlab, aralia, steven), so the polling idiom and the stuck-run
    sweeper work unchanged.
    """

    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.CASCADE,
                              related_name="scan_runs")
    file = models.ForeignKey("vault.VaultFile", on_delete=models.CASCADE,
                             related_name="scan_runs")
    status = models.CharField(max_length=10, choices=RunStatus.choices,
                              default=RunStatus.PENDING)
    error = models.TextField(blank=True)
    #: The verdict this run produced, for the poll to hand back. SET_NULL: a
    #: verdict outlives its run and a run row is prunable history.
    result = models.ForeignKey(ScanResult, null=True, blank=True,
                               on_delete=models.SET_NULL, related_name="runs")

    task_id = models.CharField(max_length=255, blank=True)
    #: Not an FK — toto.workflows must stay optional. Same trade as AiRun.
    workflow_run_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = "scan run"

    def __str__(self):
        return f"{self.owner} → {self.file_id} · {self.status}"

    @property
    def is_finished(self) -> bool:
        return self.status in (RunStatus.SUCCESS, RunStatus.FAILED)

    def finish(self, *, status, error: str = "", result=None) -> None:
        from django.utils import timezone

        self.status = status
        self.error = error[:2000]
        if result is not None:
            self.result = result
        self.finished_at = timezone.now()
        self.save(update_fields=["status", "error", "result", "finished_at"])


# ---------------------------------------------------------------------------
# Metering — the standard opt-in pair; toto.quota owns no tables.
# ---------------------------------------------------------------------------

class AntivirusUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Antivirus usage event"
        verbose_name_plural = "Antivirus usage events"


class AntivirusQuotaPolicy(AbstractQuotaPolicy):
    events = AntivirusUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Antivirus quota policy"
        verbose_name_plural = "Antivirus quota policies"


class ScannerConfig(models.Model):
    """The scanner's tuning, in ONE row and ONE JSON field.

    A JSON field on purpose: a new parameter is a new key in
    ``scanners/config.DEFAULTS`` and nothing else — no migration, which is the
    entire request behind this model. What is tunable is bounded there too;
    the core refusals are not parameters.

    Staff-only by contract (the view enforces it): scanner tuning is platform
    security policy, not a personal preference — that is what
    :class:`ScanPreference` is.
    """

    params = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                   blank=True, on_delete=models.SET_NULL,
                                   related_name="+")

    class Meta:
        verbose_name = "scanner configuration"

    def __str__(self):
        return f"scanner config ({len(self.params or {})} overrides)"

    @classmethod
    def get(cls) -> "ScannerConfig":
        row, _created = cls.objects.get_or_create(pk=1)
        return row
