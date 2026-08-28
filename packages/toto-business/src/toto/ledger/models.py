"""One logical, independently verifiable append-only history.

Revived from irena's `toto.decisions`, with one structural change: the
``company`` foreign key is gone. A ledger names its owner as a
``scope_type``/``scope_uid`` pair — both fields already existed there — so this
app knows nothing about companies, and `toto.company` binds a chain to a
Company through its own thin integration layer.

Everything else is the original: the sequence and source constraints, the
save()/delete() guards, and the soft source references that let a historical
payload outlive the row it was taken from.

Three things are new, and they are what Stage 2 is actually for:

* ``payload_xml`` — the frozen artifact is canonical XML text, and that text is
  what the chain hashes. Not a dict that happens to serialize the same way.
* a **genesis block** on every chain, recording the hash algorithm and format
  version as block content, so a chain carries the rules for reading itself.
* database triggers refusing UPDATE and DELETE, behind the Python guards.
  A `save()` override protects the ORM; it does nothing about `.update()`,
  raw SQL, or a psql prompt.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from toto.core.domain import DomainEntity

from .canonical import DEFAULT_ALGORITHM, FORMAT_VERSION


class LedgerKind(models.TextChoices):
    COMPANY = "company", "Company actions"
    DEPARTMENT = "department", "Department decisions"
    VOTING = "voting", "Voting decisions"
    GENERIC = "generic", "Other"


class Ledger(DomainEntity):
    """A chain. Owned by whatever `scope_type`/`scope_uid` names, or by nobody."""

    key = models.SlugField(max_length=180)
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=24, choices=LedgerKind.choices,
                            default=LedgerKind.GENERIC)

    #: The soft owner. A dotted model label and that row's uid — soft on
    #: purpose, so this app never imports the owner and a chain outlives it.
    scope_type = models.CharField(max_length=80, blank=True)
    scope_uid = models.UUIDField(null=True, blank=True)

    #: How this chain is sealed. Recorded again in the genesis block, which is
    #: the copy a verifier reads — these two columns are a convenience for
    #: querying, and `services.chain.verify` treats genesis as authoritative.
    algorithm = models.CharField(max_length=32, default=DEFAULT_ALGORITHM)
    format_version = models.CharField(max_length=32, default=FORMAT_VERSION)

    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["scope_type", "scope_uid", "key"],
                name="bc_one_ledger_key_per_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["scope_type", "scope_uid"], name="bc_ledger_scope_idx"),
        ]

    def __str__(self):
        return self.name

    @property
    def head(self):
        return self.entries.order_by("-sequence").first()

    @property
    def length(self):
        return self.entries.count()


class LedgerEntry(DomainEntity):
    """One frozen block.

    `uid` (from DomainEntity) is the globally unique block id. It is what a
    message body cites when it refers to an earlier block — and a citation is
    ordinary text, never a foreign key: this app does not interpret
    relationships, corrections or reversals, and a chain that resolved
    references would be doing exactly that.
    """

    #: The first block on every chain. Its payload records how to read the rest.
    GENESIS_SOURCE_TYPE = "ledger.genesis"

    ledger = models.ForeignKey(Ledger, on_delete=models.PROTECT, related_name="entries")
    sequence = models.PositiveIntegerField()
    previous_hash = models.CharField(max_length=128, blank=True)
    entry_hash = models.CharField(max_length=128, editable=False)

    #: The frozen artifact. Canonical XML text, hashed as-is.
    payload_xml = models.TextField()

    algorithm = models.CharField(max_length=32, default=DEFAULT_ALGORITHM)
    format_version = models.CharField(max_length=32, default=FORMAT_VERSION)

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bc_ledger_entries_recorded",
    )
    actor_ref = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    #: Soft references to whatever asked for this block. Soft on purpose: the
    #: historical payload must outlive an optional source app or a retired row.
    source_type = models.CharField(max_length=100, blank=True)
    source_uid = models.UUIDField(null=True, blank=True)
    source_ref = models.CharField(max_length=255, blank=True)

    #: Optional detached signature over `entry_hash`, and the key that made it.
    signature = models.TextField(blank=True)
    signature_key_id = models.CharField(max_length=128, blank=True)
    signature_algorithm = models.CharField(max_length=32, blank=True)

    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("ledger", "sequence")
        verbose_name_plural = "ledger entries"
        constraints = [
            models.UniqueConstraint(
                fields=["ledger", "sequence"],
                name="bc_one_sequence_per_ledger",
            ),
            # Fork prevention at the database, not only in the append lock:
            # two writers who both computed sequence N cannot both land.
            models.UniqueConstraint(
                fields=["ledger", "previous_hash"],
                condition=models.Q(sequence__gt=1),
                name="bc_one_successor_per_block",
            ),
            models.UniqueConstraint(
                fields=["ledger", "source_type", "source_uid"],
                condition=models.Q(source_uid__isnull=False),
                name="bc_one_source_per_ledger",
            ),
        ]
        indexes = [
            models.Index(fields=["source_type", "source_uid"], name="bc_entry_source_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Ledger entries are immutable.")
        if not self.entry_hash:
            raise ValidationError(
                "Ledger entries must be appended through the ledger engine."
            )
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted.")

    @property
    def is_genesis(self):
        return self.source_type == self.GENESIS_SOURCE_TYPE

    def __str__(self):
        return f"{self.ledger} #{self.sequence}"


# ---------------------------------------------------------------------------
# Checkpoints — the chain's head, foldable onto paper. Unparked from
# limbo/polls_governance/checkpoint_models.py.
# ---------------------------------------------------------------------------


class LedgerCheckpoint(models.Model):
    """The stored half of a ledger checkpoint.

    The row is a CONVENIENCE, not the evidence. The point of a checkpoint is
    the copy that leaves the database — the printed QR, the screenshot in a
    safe — because an attacker who can rewrite blocks can rewrite this table
    too. Storing them anyway buys three things: the list page that shows when
    each was taken and whether it still verifies, the bracketing walk that
    dates a self-consistent rewrite, and a payload to re-render as a QR
    without recomputing.

    Append-only like the blocks it guards: a checkpoint that could be edited
    would be one more thing the attacker rewrites.
    """

    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    #: How many blocks the fold covered, and where it ended.
    entry_count = models.PositiveIntegerField()
    head_hash = models.CharField(max_length=64)
    #: The algorithm tag from checkpoint.ALGORITHM — carried per row so a
    #: future algorithm change verifies old rows with the right arm.
    algorithm = models.CharField(max_length=32)

    #: The exact QR text, verbatim. What was printed is what is stored; the
    #: verifier accepts either and they cannot drift.
    payload = models.TextField()

    taken_at = models.DateTimeField(default=timezone.now)
    taken_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="bc_ledger_checkpoints_taken",
    )
    note = models.CharField(
        max_length=200, blank=True,
        help_text="Why this checkpoint was taken, e.g. 'before the 2026 audit'.",
    )

    # Independent signers are the intended shape: several rows for the same
    # count, each signed by a different key.
    signature = models.CharField(max_length=128, blank=True)
    signed_key_id = models.CharField(max_length=64, blank=True)
    signed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="bc_ledger_checkpoints_signed",
    )

    class Meta:
        ordering = ["-taken_at", "-pk"]
        indexes = [
            models.Index(fields=["scope_type", "scope_id", "-entry_count"],
                         name="bc_checkpoint_scope_idx"),
        ]
        verbose_name = "ledger checkpoint"
        verbose_name_plural = "ledger checkpoints"

    def __str__(self):
        return (f"checkpoint @{self.entry_count} "
                f"({self.scope_type or 'global'}) {self.head_hash[:12]}…")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "A checkpoint is never edited. Take a new one instead.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "A checkpoint is never deleted; it is the chain's witness.")
