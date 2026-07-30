"""datalink's own tables: who we trust, what we ran, and what the two sides agreed.

**None of these may carry a field named ``uid``.** ``backup_engine.is_backup_model()``
decides whether a model belongs in a backup archive purely by asking whether it has a
field with that exact name — for every app named in ``settings.APPS_TO_SYNC``. So a host
that added "datalink" to that list would serialise its peer grants, magic tokens and api
keys into a signed, pullable ZIP. The identity columns are therefore ``grant_uid``,
``peer_uid`` and ``run_uid``; ``DatalinkConfig.ready()`` asserts it, and a test asserts
it independently of the app loading.

The credential pair is deliberately **two directional models, not one symmetric row**.
A host can be a source for one peer while pulling from another, and rotating the key it
*presents* must not touch the key it *accepts*. ``DatalinkGrant`` is "a peer I let read
me" and holds only a hash; ``DatalinkPeer`` is "a peer I pull from" and holds a secret
it presents. The SSO connection bundles are directional for the same reason.
"""
from __future__ import annotations

import secrets
import uuid

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.urls import reverse
from django.utils import timezone


def _default_expiry():
    """Seven days, matching the backup app's stored-pull default.

    A grant is a live credential that travels through a chat window or an email during
    pairing, so it expires by default and is extended deliberately once the link works.
    """
    return timezone.now() + timezone.timedelta(days=7)


class DatalinkGrant(models.Model):
    """A peer we let read this instance. Server side; holds a hash, never a secret.

    Field for field from ``backup.StoredBackup``, whose two-factor URL plus hashed
    api-key is already a working authenticated pull channel between two toto instances.
    The check *order* that goes with it matters as much as the fields — see
    ``peer_views._resolve_grant``.
    """

    grant_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(
        max_length=180,
        help_text="The peer this grant is for, e.g. 'aurelian'. Shown to operators only.",
    )
    magic_token = models.CharField(
        max_length=128,
        unique=True,
        default=secrets.token_urlsafe,
        help_text=(
            "High-entropy URL segment, checked before the api key. Its purpose is to "
            "make the expensive key verification unreachable by guessing."
        ),
    )
    api_key_hash = models.CharField(max_length=255, blank=True)
    api_key_hint = models.CharField(
        max_length=16,
        blank=True,
        help_text="Last characters of the key, so an operator can tell which one is live.",
    )
    scopes = models.JSONField(
        default=list,
        blank=True,
        help_text=(
            "Stage keys this peer may read, from the datalink registry. An empty list "
            "grants nothing."
        ),
    )

    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True, default=_default_expiry)
    key_rotated_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="datalink_grants_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    last_read_at = models.DateTimeField(null=True, blank=True)
    read_count = models.PositiveIntegerField(default=0)
    last_peer_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="Where the last read came from, so a human notices a new source.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "data link grant"

    def __str__(self):
        return f"grant to {self.label}"

    def issue_api_key(self, raw_key=None):
        """Mint a key, store only its hash, and return the raw value once."""
        raw_key = raw_key or secrets.token_urlsafe(48)
        self.api_key_hash = make_password(raw_key)
        self.api_key_hint = raw_key[-8:]
        return raw_key

    def verify_api_key(self, raw_key):
        """PBKDF2 at the project's iteration count — deliberately the LAST check.

        At 600k iterations this is ~100ms of CPU on an unauthenticated endpoint. The
        SSO provider learned that the hard way: a guessable client id plus this call
        saturated a host at about 35 requests a second. Everything cheap runs first.
        """
        if not raw_key or not self.api_key_hash:
            return False
        return check_password(raw_key, self.api_key_hash)

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def can_be_read(self):
        return self.is_active and not self.is_expired

    def grants(self, stage: str) -> bool:
        return stage in (self.scopes or [])

    def base_path(self):
        return reverse(
            "datalink:peer_manifest",
            kwargs={"grant_uid": self.grant_uid, "magic_token": self.magic_token},
        )


class DatalinkPeer(models.Model):
    """A peer we pull from. Receiver side; holds the secret we present.

    ``api_key`` is a plain column. The precedent is
    ``sso_client.OIDCProviderConfig.client_secret``, which is the established shape for
    "a secret this host presents to a peer". Encrypting it under gervazy would make
    every pull a keyed operation needing a strongbox password, and would give a
    toto-base app a hard dependency on gervazy for one field. Read access to this table
    is read access to the credential, and that is stated rather than obscured.
    """

    peer_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    label = models.CharField(max_length=180)
    base_url = models.URLField(
        help_text="The peer's root, e.g. https://aurelian.example.org",
    )

    # What the peer issued us, from its DatalinkGrant.
    grant_uid = models.UUIDField()
    magic_token = models.CharField(max_length=128)
    api_key = models.CharField(max_length=255)
    scopes = models.JSONField(
        default=list, blank=True,
        help_text="What the peer said it granted. Advisory — the peer enforces it.",
    )

    is_active = models.BooleanField(default=True)
    probe_error = models.TextField(
        blank=True,
        help_text=(
            "Why the last handshake failed. Set at pairing time so a wrong key, an "
            "expired grant or a version mismatch surfaces immediately rather than "
            "during a run."
        ),
    )

    # Observed at the last successful handshake.
    peer_site_name = models.CharField(max_length=180, blank=True)
    peer_schema_version = models.PositiveIntegerField(default=0)
    peer_registry_digest = models.CharField(max_length=64, blank=True)
    peer_auth_posture = models.JSONField(default=dict, blank=True)

    paired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="datalink_peers_paired",
    )
    paired_at = models.DateTimeField(auto_now_add=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    pull_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-paired_at"]
        verbose_name = "data link peer"
        constraints = [
            models.UniqueConstraint(
                fields=["base_url", "grant_uid"], name="datalink_one_peer_per_grant",
            ),
        ]

    def __str__(self):
        return f"peer {self.label}"


class DatalinkRun(models.Model):
    """One operator-driven convergence, stage by stage."""

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_AWAITING_OPERATOR = "awaiting_operator"
    STATUS_AWAITING_CONFLICTS = "awaiting_conflicts"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_AWAITING_OPERATOR, "Awaiting operator"),
        (STATUS_AWAITING_CONFLICTS, "Awaiting conflict decisions"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
    ]
    TERMINAL = frozenset({STATUS_SUCCESS, STATUS_FAILED, STATUS_CANCELLED})
    # States where nothing is happening but the run is not over. The poller stops on
    # these too — there is nothing to poll for until the operator acts.
    QUIESCENT = TERMINAL | {STATUS_AWAITING_OPERATOR, STATUS_AWAITING_CONFLICTS}

    run_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # PROTECT, not CASCADE: revoking a peer must not silently delete the record of what
    # was pulled from it. Revocation is is_active=False.
    peer = models.ForeignKey(DatalinkPeer, on_delete=models.PROTECT, related_name="runs")
    status = models.CharField(
        max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True,
    )

    # The peer's manifest exactly as served, frozen at preflight. This is the run's
    # plan: what the operator was shown when they decided to start.
    manifest = models.JSONField(default=dict, blank=True)
    peer_schema_version = models.PositiveIntegerField(default=0)
    peer_registry_digest = models.CharField(max_length=64, blank=True)
    local_registry_digest = models.CharField(max_length=64, blank=True)
    peer_auth_posture = models.JSONField(default=dict, blank=True)
    clock_skew_seconds = models.IntegerField(
        null=True, blank=True,
        help_text=(
            "Peer clock minus ours at preflight. A timestamp tiebreak under unknown "
            "skew is a coin flip, so a later forensic must be able to see it was."
        ),
    )
    warnings = models.JSONField(default=list, blank=True)

    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="datalink_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "data link run"

    def __str__(self):
        return f"run {self.pk} against {self.peer.label}"

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL


class DatalinkStageRun(models.Model):
    """One stage of one run: its counters, its cursor, and what it skipped.

    ``total_rows`` being null is the single switch between a determinate and an
    indeterminate progress bar — a stage the peer could not count cheaply reports null
    and the UI stops pretending to know a percentage.
    """

    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_RUNNING, "Running"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    run = models.ForeignKey(DatalinkRun, on_delete=models.CASCADE, related_name="stages")
    stage_key = models.CharField(max_length=32)
    title = models.CharField(max_length=120, blank=True)
    order = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True,
    )

    total_rows = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="From the peer's manifest. Null means the bar is indeterminate.",
    )
    rows_read = models.PositiveIntegerField(default=0)
    rows_created = models.PositiveIntegerField(default=0)
    rows_updated = models.PositiveIntegerField(default=0)
    rows_skipped = models.PositiveIntegerField(default=0)
    rows_conflicted = models.PositiveIntegerField(default=0)
    pages_read = models.PositiveIntegerField(default=0)

    # Per-model resume state: {"people.Person": {"cursor": "...", "done": false}}.
    # Written in the SAME transaction as the rows of the page it describes, which is
    # what makes both the resume and the live progress bar honest.
    cursors = models.JSONField(default=dict, blank=True)
    # Deferred nullable self-references awaiting their target, persisted per page so a
    # resume rebuilds them: [{"model": ..., "identity": ..., "field": ..., "ref": ...}]
    deferred = models.JSONField(default=list, blank=True)
    # What this stage chose not to write, and why:
    # [{"kind": ..., "label": ..., "reason": ...}] — capped, with a "+N more" tail.
    skips = models.JSONField(default=list, blank=True)
    counts = models.JSONField(
        default=dict, blank=True,
        help_text="Tallies by reason, including every `omitted` reason the peer sent.",
    )

    stderr = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["order", "pk"]
        unique_together = [("run", "stage_key")]
        verbose_name = "data link stage"

    def __str__(self):
        return f"{self.stage_key} of run {self.run_id}"

    @property
    def percent(self):
        """None when the total is unknown — the indeterminate case."""
        if self.status == self.STATUS_SUCCESS:
            return 100
        if not self.total_rows:
            return None
        return min(100, round(100 * self.rows_read / self.total_rows))


class DatalinkMergeBase(models.Model):
    """What the peer and this instance looked like when they last agreed.

    The three-way merge base, and the reason "newest change wins" is implementable at
    all: most of the replicated scope has no modification timestamp, so the engine
    cannot ask "which edit is newer?" — it asks "who edited since we last agreed?".

    Two checksums, not one, and the pair is essential rather than tidy. ``save()``
    overrides transform a row as it lands: ``locations.Address`` re-derives lat/lon from
    geometry, and several models generate slugs. With a single checksum, the next run
    reads every row of those models as locally edited and the operator faces a conflict
    storm across a third of the scope.
    """

    peer = models.ForeignKey(
        DatalinkPeer, on_delete=models.CASCADE, related_name="merge_bases",
    )
    model_label = models.CharField(max_length=100, db_index=True)
    identity_hash = models.CharField(max_length=64)
    identity = models.JSONField(
        default=dict, help_text="The readable identity, for forensics and display.",
    )

    peer_checksum = models.CharField(
        max_length=64, help_text="What the peer's payload hashed to when accepted.",
    )
    local_checksum = models.CharField(
        max_length=64,
        help_text=(
            "What the local row hashed to, projected through the same policy, right "
            "after it was written. Differs from peer_checksum whenever save() "
            "transformed the row — which is why both are stored."
        ),
    )
    peer_changed_at = models.DateTimeField(null=True, blank=True)
    # CharField because primary keys in the replicated scope are int OR uuid.
    local_pk = models.CharField(max_length=64, blank=True)

    last_run = models.ForeignKey(
        DatalinkRun, on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    accepted_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("peer", "model_label", "identity_hash")]
        indexes = [
            models.Index(fields=["peer", "model_label"]),
            models.Index(fields=["peer", "model_label", "identity_hash"]),
        ]
        verbose_name = "data link merge base"

    def __str__(self):
        return f"{self.model_label} {self.identity_hash[:12]}"


class DatalinkConflict(models.Model):
    """Something the engine refused to decide on its own.

    ``payload`` holds the peer's row as received, so a decision can be made days later
    with the peer unreachable — the operator is never forced to choose while a
    connection is open.
    """

    KIND_BOTH_CHANGED = "both_changed"
    KIND_NO_BASE_DIVERGENCE = "no_base_divergence"
    KIND_DELETED_LOCALLY = "deleted_locally"
    KIND_UNRESOLVED_REFERENCE = "unresolved_reference"
    KIND_UNIQUE_COLLISION = "unique_guard_collision"
    KIND_UNCLAIMED_PERSON = "unclaimed_person"
    KIND_CYCLE = "cycle"
    KIND_WRITE_ERROR = "write_error"
    KIND_CHOICES = [
        (KIND_BOTH_CHANGED, "Both sides changed"),
        (KIND_NO_BASE_DIVERGENCE, "Differs, and never synced before"),
        (KIND_DELETED_LOCALLY, "Deleted here since the last sync"),
        (KIND_UNRESOLVED_REFERENCE, "Points at something that did not arrive"),
        (KIND_UNIQUE_COLLISION, "Collides with a different local row"),
        (KIND_UNCLAIMED_PERSON, "Profile with no account attached"),
        (KIND_CYCLE, "Circular parent reference"),
        (KIND_WRITE_ERROR, "Could not be written"),
    ]

    SEVERITY_BLOCKING = "blocking"
    SEVERITY_WARNING = "warning"
    SEVERITY_INFO = "info"
    SEVERITY_CHOICES = [
        (SEVERITY_BLOCKING, "Blocking"),
        (SEVERITY_WARNING, "Warning"),
        (SEVERITY_INFO, "Informational"),
    ]

    RESOLUTION_PENDING = "pending"
    RESOLUTION_TAKE_PEER = "take_peer"
    RESOLUTION_KEEP_LOCAL = "keep_local"
    RESOLUTION_SKIP = "skip"
    RESOLUTION_ACKNOWLEDGED = "acknowledged"
    RESOLUTION_CHOICES = [
        (RESOLUTION_PENDING, "Undecided"),
        (RESOLUTION_TAKE_PEER, "Take the peer's"),
        (RESOLUTION_KEEP_LOCAL, "Keep local"),
        (RESOLUTION_SKIP, "Skip this row"),
        (RESOLUTION_ACKNOWLEDGED, "Acknowledged"),
    ]

    run = models.ForeignKey(DatalinkRun, on_delete=models.CASCADE, related_name="conflicts")
    stage_key = models.CharField(max_length=32, db_index=True)
    model_label = models.CharField(max_length=100, db_index=True)
    identity = models.JSONField(default=dict)
    identity_hash = models.CharField(max_length=64, db_index=True)

    kind = models.CharField(max_length=32, choices=KIND_CHOICES, db_index=True)
    severity = models.CharField(
        max_length=12, choices=SEVERITY_CHOICES, default=SEVERITY_BLOCKING,
    )
    label = models.CharField(
        max_length=255, blank=True,
        help_text=(
            "A human name for the row, taken from the PEER's data. Peer-supplied, so it "
            "is escaped everywhere it renders and never marked safe."
        ),
    )
    field_diff = models.JSONField(
        default=dict, blank=True, help_text='{"field": {"local": ..., "peer": ...}}',
    )
    payload = models.JSONField(
        default=dict, blank=True, help_text="The peer's row, so a decision needs no refetch.",
    )
    detail = models.TextField(blank=True)

    resolution = models.CharField(
        max_length=16, choices=RESOLUTION_CHOICES, default=RESOLUTION_PENDING, db_index=True,
    )
    resolution_reason = models.CharField(
        max_length=64, blank=True,
        help_text=(
            "How it was decided: 'operator', 'bulk', or 'timestamp'. A timestamp "
            "tiebreak writes a row here even though it resolved itself, so an "
            "auto-resolution is always visible rather than silent."
        ),
    )
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="datalink_conflicts_resolved",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    applied = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("run", "model_label", "identity_hash", "kind")]
        indexes = [
            models.Index(fields=["run", "stage_key", "severity"]),
            models.Index(fields=["run", "resolution"]),
        ]
        verbose_name = "data link conflict"

    def __str__(self):
        return f"{self.kind} on {self.model_label}"


class DatalinkIdentityMap(models.Model):
    """Operator-approved equivalences: the same entity under two different identities.

    Two instances that never synced generate different ``uid``s for the same person, so
    the first run finds a Person whose uid is unknown but whose unique slug already
    belongs to somebody. That is a reviewable collision, not an error and not a merge to
    perform silently. Once an operator approves the pairing, this table redirects the
    peer's identity to the local row and the next run updates instead of inserting.
    """

    peer = models.ForeignKey(
        DatalinkPeer, on_delete=models.CASCADE, related_name="identity_map",
    )
    model_label = models.CharField(max_length=100, db_index=True)
    peer_identity = models.JSONField(default=dict)
    peer_identity_hash = models.CharField(max_length=64)
    local_identity = models.JSONField(default=dict)
    local_pk = models.CharField(max_length=64)
    note = models.TextField(blank=True)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="datalink_aliases_approved",
    )
    approved_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("peer", "model_label", "peer_identity_hash")]
        verbose_name = "data link identity alias"

    def __str__(self):
        return f"{self.model_label} alias"
