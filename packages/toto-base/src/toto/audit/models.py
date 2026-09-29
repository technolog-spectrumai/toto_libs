"""A hash-chained, append-only record of what happened.

Adapted from the parked Django Irena's ``toto.audit``. Each record hashes its
own material together with its predecessor's digest, so removing or editing a
row anywhere in the chain is detectable by ``verify_chain`` — a deleted record
breaks the successor's ``previous_hash``, and an edited one no longer hashes to
its own ``record_hash``.

This is audit *metadata*: who did what to which row, and when. It is not
Irena's governance ledger and makes none of its claims — Irena's chain is
signed with Ed25519 keys and is the thing a decision's validity rests on. This
one is unsigned, and answers a smaller question: has this platform's own record
of its own edits been tampered with.
"""

import uuid

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

#: One chain per host: each host has its own database, so a second chain would
#: only be a second thing to verify.
#:
#: The value is historical and deliberately unchanged. This app was placidia's
#: before it moved into the suite, and that host's live chain is keyed on this
#: exact string — a tidier default would orphan every record written before the
#: move. A host that wants its own key sets ``AUDIT_CHAIN_KEY``.
DEFAULT_CHAIN_KEY = "placidia-activity"


def chain_key() -> str:
    """The key `record()` opens its chain under."""
    from django.conf import settings

    return getattr(settings, "AUDIT_CHAIN_KEY", "") or DEFAULT_CHAIN_KEY


class AuditChain(models.Model):
    key = models.SlugField(max_length=80, unique=True, default=DEFAULT_CHAIN_KEY)
    name = models.CharField(max_length=160, default="Placidia activity audit")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class AuditRecord(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    chain = models.ForeignKey(AuditChain, on_delete=models.PROTECT, related_name="records")
    sequence = models.PositiveBigIntegerField()
    previous_hash = models.CharField(max_length=64, blank=True)
    record_hash = models.CharField(max_length=64, editable=False)
    algorithm = models.CharField(max_length=32, default="sha256-json-chain-1")
    timestamp = models.DateTimeField(default=timezone.now)

    #: The actor's id, kept as it was when the record was written — NEVER
    #: nulled (2026-09-29). The digest covers ``actor_user_id``
    #: (``record_material``), so the SET_NULL this used to be broke the chain's
    #: verification for every record an account had acted on, the moment that
    #: account was deleted — and since sign-ins are recorded, that is nearly
    #: every account. DO_NOTHING with no database constraint: a deleted
    #: account leaves its id behind as a plain number (``select_related`` then
    #: reads None), and the chain keeps verifying.
    actor_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.DO_NOTHING, db_constraint=False,
        related_name="placidia_audit_records",
    )
    #: The username is denormalised on purpose: deleting an account must not
    #: erase who did the thing.
    actor_username = models.CharField(max_length=150, blank=True)

    action = models.CharField(max_length=100, db_index=True)
    app_label = models.CharField(max_length=100, db_index=True)
    object_type = models.CharField(max_length=150, blank=True, db_index=True)
    object_id = models.CharField(max_length=255, blank=True, db_index=True)
    object_description = models.CharField(max_length=500, blank=True)
    content_type = models.ForeignKey(
        ContentType, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    changes = models.JSONField(default=dict, blank=True)
    request_source = models.JSONField(default=dict, blank=True)
    correlation_id = models.CharField(max_length=128, blank=True, db_index=True)
    source = models.CharField(max_length=80, default="system")
    success = models.BooleanField(default=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=255, null=True, blank=True, unique=True)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-timestamp", "-sequence")
        constraints = [
            models.UniqueConstraint(fields=("chain", "sequence"),
                                    name="placidia_audit_sequence_per_chain"),
        ]
        indexes = [
            models.Index(fields=("object_type", "object_id", "-timestamp"),
                         name="placidia_audit_object_idx"),
            models.Index(fields=("app_label", "action", "-timestamp"),
                         name="placidia_audit_action_idx"),
        ]

    def __str__(self):
        return f"#{self.sequence} {self.action} [{'OK' if self.success else 'FAIL'}]"

    def save(self, *args, **kwargs):
        """Append-only, enforced in Python because SQL has no "insert only".

        The ``record_hash`` guard is the second half: a record can only be
        created by :func:`audit.services.record`, which is the only code that
        knows how to compute it. Constructing an AuditRecord by hand and
        saving it fails here rather than landing an unverifiable row.
        """
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            raise ValidationError("Audit records are append-only and cannot be edited.")
        if not self.record_hash:
            raise ValidationError("Audit records must be appended through audit.services.record.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit records are append-only and cannot be deleted.")
