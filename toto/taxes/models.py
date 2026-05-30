"""
toto.taxes — transaction fees on asset transfers.

A TransactionFeePolicy defines a fee rate (in basis points) or a fixed amount
applied to LedgerTransactions involving a specific asset. When a transaction
fires, the fee is materialised as a TransactionFee record and the corresponding
ledger entries are written by the calling code.

Treasury reads these models for the financial dashboard. No economy apps depend
on taxes — it depends only on assets.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import models


class FeeScope(models.TextChoices):
    ALL = "all", "All transactions"
    EXTERNAL = "external", "External transfers only"
    INTERNAL = "internal", "Internal transfers only"


class TransactionFeePolicy(models.Model):
    """Defines a fee rule applied to a specific asset's transactions."""

    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="fee_policies",
    )
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    fee_rate_bps = models.PositiveIntegerField(
        default=0,
        help_text="Fee in basis points (1 bps = 0.01%). Applied proportionally to transaction amount.",
    )
    fixed_amount_base_units = models.BigIntegerField(
        default=0,
        help_text="Fixed fee in asset base units applied per transaction (on top of rate).",
    )
    receiving_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="tax_receiving_policies",
        help_text="Account that receives the collected fee.",
    )
    scope = models.CharField(max_length=20, choices=FeeScope.choices, default=FeeScope.ALL)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Transaction Fee Policy"
        verbose_name_plural = "Transaction Fee Policies"
        ordering = ["asset__unit_name", "name"]

    def __str__(self):
        return f"{self.name} ({self.asset.unit_name})"

    def calculate_fee(self, transaction_amount_base_units: int) -> int:
        """Return the fee amount in base units for a given transaction amount."""
        rate_fee = int(Decimal(transaction_amount_base_units) * Decimal(self.fee_rate_bps) / Decimal(10000))
        return rate_fee + self.fixed_amount_base_units


class TransactionFee(models.Model):
    """A fee charge materialised for a specific LedgerTransaction."""

    policy = models.ForeignKey(
        TransactionFeePolicy,
        on_delete=models.PROTECT,
        related_name="charges",
    )
    transaction = models.ForeignKey(
        "assets.LedgerTransaction",
        on_delete=models.PROTECT,
        related_name="tax_charges",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="tax_charges",
    )
    amount_base_units = models.BigIntegerField()
    transaction_amount_base_units = models.BigIntegerField(
        help_text="The gross transaction amount the fee was calculated from.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Transaction Fee"
        verbose_name_plural = "Transaction Fees"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["asset", "created_at"], name="taxes_fee_asset_created_idx"),
            models.Index(fields=["policy", "created_at"], name="taxes_fee_policy_created_idx"),
        ]

    def __str__(self):
        return f"Fee {self.amount_base_units} {self.asset.unit_name} on tx#{self.transaction_id}"
