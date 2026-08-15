"""The stored half of a ledger checkpoint.

The row is a CONVENIENCE, not the evidence. The point of a checkpoint is the
copy that leaves the database — the printed QR, the screenshot in a safe —
because an attacker who can rewrite Decisions can rewrite this table too.
Storing checkpoints anyway buys three things: the list page that shows when
each was taken and whether it still verifies, the bracketing walk that dates
a self-consistent rewrite (the newest stored checkpoint that still matches
bounds the last honest state), and a payload to re-render as a QR without
recomputing.

Append-only like the Decision it guards: a checkpoint that could be edited
would be one more thing the attacker rewrites.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class LedgerCheckpoint(models.Model):
    scope_type = models.CharField(max_length=40, blank=True, db_index=True)
    scope_id = models.CharField(max_length=64, blank=True, db_index=True)

    #: How many decisions the fold covered, and where it ended.
    entry_count = models.PositiveIntegerField()
    head_hash = models.CharField(max_length=64)
    #: The algorithm tag from checkpoint.ALGORITHM — carried per row so a
    #: future algorithm change verifies old rows with the right arm.
    algorithm = models.CharField(max_length=32)

    #: The exact QR text, verbatim. What was printed is what is stored; the
    #: verifier accepts either and they cannot drift.
    payload = models.TextField()

    taken_at = models.DateTimeField(default=timezone.now)
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                 blank=True, on_delete=models.SET_NULL,
                                 related_name="ledger_checkpoints_taken")
    note = models.CharField(max_length=200, blank=True, help_text=_(
        "Why this checkpoint was taken, e.g. 'before the 2026 audit'."))

    # -- optional signature (independent signers are the intended shape:
    #    several rows for the same count, each signed by a different key) ----
    signature = models.CharField(max_length=128, blank=True)
    signed_key_id = models.CharField(max_length=64, blank=True)
    signed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                  blank=True, on_delete=models.SET_NULL,
                                  related_name="ledger_checkpoints_signed")

    class Meta:
        ordering = ["-taken_at", "-pk"]
        indexes = [models.Index(
            fields=["scope_type", "scope_id", "-entry_count"])]
        verbose_name = _("ledger checkpoint")
        verbose_name_plural = _("ledger checkpoints")

    def __str__(self):
        return (f"checkpoint @{self.entry_count} "
                f"({self.scope_type or 'global'}) {self.head_hash[:12]}…")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError(
                "A checkpoint is never edited. Take a new one instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(
            "A checkpoint is never deleted; it is the ledger's witness.")
