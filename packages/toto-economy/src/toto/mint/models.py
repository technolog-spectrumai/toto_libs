"""The record of who created money, and why.

The ledger says what moved: an ASSET_CREATE transaction, hash-chained like
everything else. It does not say who authorised it or on what grounds, and
issuance is irreversible — so this is the row that makes it accountable.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class IssuanceRecord(models.Model):
    """Append-only. One row per asset ever issued here."""

    asset = models.OneToOneField("assets.Asset", on_delete=models.PROTECT,
                                 related_name="issuance")
    #: Denormalised so the record stands alone in an export or an audit, where
    #: a local pk means nothing.
    currency_hash = models.CharField(max_length=71)
    unit_name = models.CharField(max_length=20)
    total_supply_base_units = models.PositiveBigIntegerField()
    decimals = models.PositiveSmallIntegerField()

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="asset_issuances")
    reason = models.TextField()
    genesis_payload = models.JSONField(default=dict)
    genesis_signature = models.TextField()
    ledger_transaction = models.ForeignKey(
        "assets.LedgerTransaction", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="issuance_records")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        verbose_name = "issuance record"

    def __str__(self):
        return f"{self.unit_name} — {self.currency_hash[:16]}…"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "An issuance record describes something that already "
                "happened; it cannot be edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The issuance log is append-only.")


class LedgerAudit(models.Model):
    """One remote audit of a branch's books. Append-only.

    Four outcomes, and the last two are deliberately distinct:

    * ``ok``            — checked, and the arithmetic agrees.
    * ``discrepancy``   — checked, and it does not.
    * ``unverifiable``  — the statement's signature does not verify, or there
      is no contract to check it against.
    * ``unreachable``   — the branch did not answer.

    Conflating the last two with ``ok`` is how an unreachable branch quietly
    becomes a passing one. Nothing here changes the branch's state: a failure
    is recorded and flagged, and what happens next is a person's decision.
    """

    node_id = models.CharField(max_length=200)
    outcome = models.CharField(max_length=16)
    findings = models.JSONField(default=list, blank=True)
    statement = models.JSONField(default=dict, blank=True)
    expected_allocated_base_units = models.BigIntegerField(default=0)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="ledger_audits")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        indexes = [models.Index(fields=["node_id", "-created_at"],
                                name="mint_audit_node_idx")]

    def __str__(self):
        return f"{self.node_id}: {self.outcome}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError("An audit records a check that already ran.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("The audit log is append-only.")
