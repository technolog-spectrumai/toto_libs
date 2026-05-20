from decimal import Decimal

from django.conf import settings
from django.db import models

from toto.assets.models import from_base_units


class ExchangeRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class AssetExchangeRequest(models.Model):
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_exchange_requests_made",
    )
    counterparty = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="asset_exchange_requests_received",
    )
    requester_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="exchange_requests_made",
    )
    counterparty_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="exchange_requests_received",
    )
    offer_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="exchange_requests_offered",
    )
    request_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="exchange_requests_requested",
    )
    offer_amount_base_units = models.BigIntegerField()
    request_amount_base_units = models.BigIntegerField()
    commission_amount_base_units = models.BigIntegerField(default=0)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=12)
    commission_percent = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("0.0000"))
    status = models.CharField(
        max_length=20,
        choices=ExchangeRequestStatus.choices,
        default=ExchangeRequestStatus.PENDING,
    )
    offer_tx_reference = models.CharField(max_length=255, blank=True)
    request_tx_reference = models.CharField(max_length=255, blank=True)
    commission_tx_reference = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    response_note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "assets_assetexchangerequest"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["requester", "status"], name="assets_asse_request_a228da_idx"),
            models.Index(fields=["counterparty", "status"], name="assets_asse_counter_eaf679_idx"),
            models.Index(fields=["offer_asset", "request_asset"], name="assets_asse_offer_a_5e3837_idx"),
            models.Index(fields=["created_at"], name="assets_asse_created_a55a97_idx"),
        ]

    def __str__(self):
        return (
            f"{self.requester} offers {self.offer_amount_display} {self.offer_asset.unit_name} "
            f"for {self.request_amount_display} {self.request_asset.unit_name}"
        )

    @property
    def offer_amount_display(self):
        return from_base_units(self.offer_amount_base_units, self.offer_asset.decimals)

    @property
    def request_amount_display(self):
        return from_base_units(self.request_amount_base_units, self.request_asset.decimals)

    @property
    def commission_amount_display(self):
        return from_base_units(self.commission_amount_base_units, self.offer_asset.decimals)

    @property
    def requester_total_display(self):
        return self.offer_amount_display + self.commission_amount_display
