"""Snapshots: a cryptographic state of one electorate's Decision Ledger.

A snapshot is **not** an event and **not** somebody's document. It is a STATE
— the head of one roll's hash chain at some count of entries. Two people
asking for a snapshot of the same unchanged ledger are asking about the same
state, so they get the same snapshot row; a snapshot is discovered, not
created. That is why identity is ``(electorate, head_hash)`` and why the
timestamp is the ledger's own last modification rather than the moment
somebody pressed a button: pressing a button is not a fact about the ledger.

**The stored row is a convenience; the QR is the evidence.** Verification
never consults this table — it parses the payload, recomputes the chain from
stored Decisions, and compares. So a snapshot row may be deleted freely (it
frees a QR image, nothing else), and a QR printed a year ago still verifies
against a database whose snapshot rows were all wiped. Deleting a snapshot
cannot touch the Decision Ledger; nothing here writes to it.

The fold, the netstring preimage and the MATCH/DIFFERS reporting are shared
with :mod:`toto.polls.checkpoint`, which owns the cryptography. This module
owns what a snapshot IS.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from . import checkpoint


def ledger_of(electorate):
    """One electorate's ledger, oldest first. The ONLY door to a chain."""
    from .models import Decision

    if electorate is None:
        return Decision.objects.none()
    return Decision.objects.filter(electorate=electorate).order_by("pk")


def head_of(electorate):
    """``(entry_count, head_hex, last_modified)`` for one roll's ledger.

    ``last_modified`` is the ledger's own clock — the newest ``decided_at``
    among the entries the fold covered — because that is when this STATE came
    into being. A snapshot taken today of a ledger untouched since March is a
    snapshot of March's state, and dating it today would misreport the only
    thing it exists to attest.
    """
    count, head = 0, ""
    # One pass, in pk order — the fold is a running hash, so the last tuple
    # it yields IS the head at the full count.
    for c, h, _pk in checkpoint.fold_heads(ledger_of(electorate).iterator()):
        count, head = c, h
    last = (ledger_of(electorate).order_by("-decided_at")
            .values_list("decided_at", flat=True).first())
    return count, head, last


def verify_chain(electorate):
    """Verify the chain this electorate's decisions belong to — its SCOPE's.

    **The walk must follow the same key the links were written with.**
    ``Decision.save()`` sets ``prev_hash`` from the previous decision in the
    SCOPE, so a walk restricted to one electorate walks a subsequence of that
    chain: as soon as a second body decides in the same scope, the second
    body's first decision points at the first body's, a per-electorate walk
    expects ``prev == ""``, and the page reports an untampered ledger as
    tampered. Not hypothetical for a company — one company is one scope with an
    assembly and a board.

    It went unnoticed because every fixture gave each electorate its own
    ``scope_id``, so the two keys never diverged in a test.

    Contrast :func:`head_of`, which folds the SAME subset and is correct: a
    fold over a subset is a self-consistent hash OF that subset, while a chain
    walk over a subset is a claim about links that were never written that way.
    """
    from .models import Decision

    if electorate is None:
        return Decision.verify_chain()
    return Decision.verify_chain(electorate.scope_type, electorate.scope_id)


def payload_for(electorate, count, head_hex, last_modified):
    """The QR text for one ledger state. Self-describing on purpose: a
    verifier needs no database row and no network to read it."""
    import os

    return checkpoint.build_payload(
        scope_type="electorate", scope_id=str(electorate.pk),
        entry_count=count, head_hex=head_hex,
        taken_at=last_modified,
        site=os.environ.get("PLATFORM_NAME", ""))


class LedgerSnapshot(models.Model):
    """One state of one electorate's ledger, stored so it can be shown.

    Unique on ``(electorate, head_hash)``: the same state is the same
    snapshot, whoever asks and however often. ``first_seen_by`` records who
    happened to ask first — a footnote, never identity.
    """

    electorate = models.ForeignKey("polls.Electorate", on_delete=models.CASCADE,
                                   related_name="ledger_snapshots")
    #: How many entries the fold covered, and where it ended.
    entry_count = models.PositiveIntegerField()
    head_hash = models.CharField(max_length=64)
    algorithm = models.CharField(max_length=32, default=checkpoint.ALGORITHM)

    #: The exact QR text. What was shown is what is stored.
    payload = models.TextField()

    #: THE LEDGER'S clock: when this state came into being, i.e. the newest
    #: decision it covers. Null only for an empty ledger.
    ledger_modified_at = models.DateTimeField(null=True, blank=True)
    #: Bookkeeping, not identity.
    first_seen_at = models.DateTimeField(auto_now_add=True)
    first_seen_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                      blank=True, on_delete=models.SET_NULL,
                                      related_name="ledger_snapshots_seen")
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-entry_count", "-first_seen_at"]
        constraints = [
            models.UniqueConstraint(fields=["electorate", "head_hash"],
                                    name="uniq_snapshot_per_ledger_state"),
        ]
        indexes = [models.Index(fields=["electorate", "-entry_count"])]
        verbose_name = _("ledger snapshot")
        verbose_name_plural = _("ledger snapshots")

    def __str__(self):
        return f"{self.electorate_id} @{self.entry_count} {self.head_hash[:12]}…"

    @property
    def is_current(self) -> bool:
        """Whether the ledger still stands where this snapshot says."""
        _count, head, _last = head_of(self.electorate)
        return head == self.head_hash


def snapshot_current(electorate, *, by=None, note=""):
    """The snapshot of this ledger's CURRENT state — found or made.

    Never a duplicate: the same state resolves to the same row, so two people
    pressing the button a second apart share one snapshot and one QR. Returns
    ``(snapshot, created)``.
    """
    count, head, last = head_of(electorate)
    if not head:
        return None, False
    snapshot, created = LedgerSnapshot.objects.get_or_create(
        electorate=electorate, head_hash=head,
        defaults={
            "entry_count": count,
            "algorithm": checkpoint.ALGORITHM,
            "payload": payload_for(electorate, count, head, last),
            "ledger_modified_at": last,
            "first_seen_by": by,
            "note": note[:200],
        })
    return snapshot, created


def verify_payload(text):
    """Compare any QR text against the ledger it names. No row required.

    This is the whole point of the feature: the checkpoint in somebody's
    pocket is evidence precisely because verifying it consults the DECISIONS,
    not a snapshot table that the same attacker could edit.
    """
    from .electorate_models import Electorate

    parsed = checkpoint.parse_payload(text)
    if parsed["scope_type"] != "electorate":
        raise checkpoint.PayloadError(
            "That checkpoint is not for an electorate's ledger.")
    electorate = Electorate.objects.filter(pk=parsed["scope_id"]).first()
    count, head, last = head_of(electorate) if electorate else (0, "", None)

    matches = bool(head) and head == parsed["head_hex"]
    return {
        "parsed": parsed,
        "electorate": electorate,
        "current_count": count,
        "current_head": head,
        "ledger_modified_at": last,
        "matches": matches,
        "verdict": "MATCH" if matches else "DIFFERS",
        "chain": verify_chain(electorate) if electorate else None,
    }
