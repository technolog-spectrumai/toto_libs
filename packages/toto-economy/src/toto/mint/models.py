"""The record of who created money, and why.

The ledger says what moved: an ASSET_CREATE transaction, hash-chained like
everything else. It does not say who authorised it or on what grounds, and
issuance is irreversible — so this is the row that makes it accountable.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from toto.assets.models import CurrencyIssuer


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


class MintEventKind(models.TextChoices):
    MINT = "mint", "Mint"
    BURN = "burn", "Burn"


class CurrencyMintEvent(models.Model):
    """One act of creation or destruction. Immutable, signed, chained.

    The ledger says units moved between accounts; this says units came into
    existence or left it. Only the master writes here, and it writes exactly
    once per act — ``supply`` is the sum over these rows and nothing else, so
    there is no cached number anywhere that could disagree with the history.

    ``prev_hash`` makes the rows a chain rather than a pile: each event names
    the one before it, so removing or reordering any of them is detectable by
    anyone holding the head. See ``toto.mint.chain`` for the arithmetic and
    portal/hierarchical_economy.md for why supply is derived at all.
    """

    #: Global and monotonic across every currency — one authority, one order.
    sequence = models.PositiveBigIntegerField(unique=True)
    kind = models.CharField(max_length=8, choices=MintEventKind.choices)

    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT,
                              related_name="mint_events")
    #: Denormalised so the event stands alone in an export or an audit, where
    #: a local pk means nothing and a ticker means less.
    currency_hash = models.CharField(max_length=71)
    #: Always positive. A burn is kind=burn, never a negative mint.
    amount_base_units = models.PositiveBigIntegerField()

    #: The previous event's hash; "" on the very first event ever written.
    prev_hash = models.CharField(max_length=71, blank=True, default="")
    event_hash = models.CharField(max_length=71, unique=True)
    signature = models.TextField()
    issuer_fingerprint = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                              on_delete=models.SET_NULL,
                              related_name="mint_events")
    reason = models.TextField()
    ledger_transaction = models.ForeignKey(
        "assets.LedgerTransaction", null=True, blank=True,
        on_delete=models.SET_NULL, related_name="mint_events")

    #: Unused today. If this platform's settlement ever moves onto a chain,
    #: these record which on-chain fact each local event corresponds to —
    #: reserved now so the correspondence can be added without a rewrite.
    external_ref = models.CharField(max_length=200, blank=True, default="")
    backend = models.CharField(max_length=40, blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sequence"]
        verbose_name = "monetary event"
        indexes = [models.Index(fields=["currency_hash", "sequence"],
                                name="mint_event_currency_idx")]

    def __str__(self):
        return (f"#{self.sequence} {self.kind} "
                f"{self.amount_base_units} {self.currency_hash[:16]}…")

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError(
                "A monetary event records something that already happened. "
                "Editing one would make the chain describe a past that never "
                "occurred; write a compensating event instead.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError(
            "The monetary history is append-only. Deleting an event would "
            "break every link after it.")

    def verify(self) -> bool:
        """Does this row's hash recompute, and does its signature hold?"""
        from .chain import compute_event_hash, verify_event

        issuer = CurrencyIssuer.objects.filter(
            fingerprint=self.issuer_fingerprint).first()
        if issuer is None:
            return False
        if compute_event_hash(self.payload) != self.event_hash:
            return False
        return verify_event(issuer.public_key_pem, self.payload,
                            self.signature)


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
