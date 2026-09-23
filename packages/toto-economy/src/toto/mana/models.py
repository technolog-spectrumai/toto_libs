"""Which asset each pool is, how fast it refills, and what has been granted.

Deliberately small. A pool's BALANCE is not stored here and never will be: it
is an ``AssetHolding`` on the member's ordinary prepaid account, moved only by
ordinary ledger transactions. What this app owns is the binding of a role to an
asset, the two dials, and the claim rows that make every grant idempotent.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Role(models.TextChoices):
    SECURITY = "security", "Security"
    COMPUTE = "compute", "Compute"
    STORAGE = "storage", "Storage"


class ManaPool(models.Model):
    """One role, bound to one asset — the FK is the functional binding.

    ``SettlementAsset``'s shape, one row per role: ``role`` is unique, so the
    database refuses a second binding rather than the code remembering to. A
    POINTER, never a copy — changing the asset moves nothing already held and
    only changes what the next grant or charge is denominated in. ``PROTECT``
    because an asset a pool is bound to must not be deleted out from under it.

    Created by ``toto.mana.bootstrap.ensure_mana`` on every ingress with
    ``get_or_create`` and never touched again, so a staff edit to either dial
    survives every deploy.
    """

    role = models.CharField(max_length=16, choices=Role.choices, unique=True)
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT,
                              related_name="+")
    #: Display units, like ``FaucetMember.amount_per_hour`` — a number a person
    #: types; converted through the asset's decimals when it pays.
    regen_per_hour = models.DecimalField(max_digits=30, decimal_places=18)
    max_pool = models.DecimalField(max_digits=30, decimal_places=18)
    chosen_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True,
                                  blank=True, on_delete=models.SET_NULL,
                                  related_name="+")
    chosen_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("role",)
        verbose_name = "mana pool"

    def __str__(self):
        return f"{self.role} → {self.asset.unit_name}"

    #: The largest base-unit value a holding can carry.
    BIGINT_MAX = 2 ** 63 - 1

    def clean(self):
        from toto.assets.models import to_base_units

        if self.regen_per_hour is not None and self.regen_per_hour < 0:
            raise ValidationError({"regen_per_hour": "Cannot be negative."})
        if self.max_pool is not None and self.max_pool <= 0:
            raise ValidationError({"max_pool": "Must be more than zero."})
        if not self.asset_id:
            return
        if not self.asset.active:
            raise ValidationError({"asset": f"{self.asset.unit_name} is not active."})
        if not self.asset.reserve_account_id:
            raise ValidationError(
                {"asset": f"{self.asset.unit_name} has no reserve to refill from."})
        if to_base_units(Decimal(self.max_pool or 0),
                         self.asset.decimals) > self.BIGINT_MAX:
            raise ValidationError({"max_pool": "Too large for this asset's decimals."})


class ManaGrant(models.Model):
    """One claim on one (member, role, key) — ``FaucetPayout``'s shape.

    The claim is written BEFORE any money moves and the database referees it:
    two workers racing the same hour collide on the unique constraint, one pays
    and the other stops. The transfer's own unique reference is the second
    layer. ``amount_base_units`` of 0 is a real outcome — "claimed, nothing
    paid" (the pool was full, the daily reward cap was reached, the reserve was
    dry) — and ``detail`` says which.

    Keys: ``hourly:2026-09-23T11`` (regen), ``encrypt:<file pk>:2026-09-23``
    (the reward), ``signup`` (the opening fill).
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="mana_grants")
    role = models.CharField(max_length=16, choices=Role.choices)
    key = models.CharField(max_length=64)
    amount_base_units = models.BigIntegerField(default=0)
    transaction = models.ForeignKey("assets.LedgerTransaction", null=True,
                                    blank=True, on_delete=models.SET_NULL,
                                    related_name="mana_grants")
    detail = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["user", "role", "key"],
                                    name="mana_one_grant_per_user_role_key"),
        ]
        indexes = [models.Index(fields=["role", "key"])]

    def __str__(self):
        return f"{self.user} {self.role} {self.key} = {self.amount_base_units}"
