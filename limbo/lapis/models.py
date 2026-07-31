"""Parked models — the Lapis smart-contract layer and Obligation.

Pulled out of the live ``toto.assets`` app on 2026-07-31: the ledger core (Asset,
LedgerAccount, holdings, transactions, hash chain, Currency, wallet) stays live;
this smart-contract + debt layer was immature and moved here.

These depend on the ledger core that remains in ``toto.assets`` — ``LedgerAccount``,
``Asset`` and ``from_base_units`` are imported from there. The Lapis VM they use lives
beside this file in ``vm/`` (moved verbatim from ``assets/lapis/``). This is parked
reference code: it is not installed, not tested, and not shipped in any wheel.
"""
from __future__ import annotations

import uuid as _uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models

from toto.assets.models import Asset, LedgerAccount, from_base_units


# ---------------------------------------------------------------------------
# Obligation — a recorded debt between two accounts, settled by a future transfer.
# ---------------------------------------------------------------------------

class ObligationQuerySet(models.QuerySet):
    def payables_for(self, account):
        return self.filter(debtor_account=account)

    def receivables_for(self, account):
        return self.filter(creditor_account=account)

    def pending(self):
        return self.filter(status="pending")

    def overdue(self):
        from django.utils import timezone
        return self.filter(status="pending", due_at__lt=timezone.now())


class ObligationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OVERDUE = "overdue", "Overdue"
    FULFILLED = "fulfilled", "Fulfilled"
    DEFAULTED = "defaulted", "Defaulted"


class Obligation(models.Model):
    objects = ObligationQuerySet.as_manager()

    reference = models.CharField(max_length=255, unique=True)
    order_reference = models.CharField(max_length=255, blank=True)
    debtor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='debtor_obligations'
    )
    creditor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='creditor_obligations'
    )
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='obligations')
    amount_base_units = models.BigIntegerField()
    due_at = models.DateTimeField()
    collateral_account = models.ForeignKey(
        LedgerAccount, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_amount_base_units = models.BigIntegerField(default=0)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ObligationStatus.choices, default=ObligationStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at']

    def __str__(self):
        return f"{self.reference} — {self.debtor_account.code} owes {self.amount_display} {self.asset.unit_name}"

    @property
    def amount_display(self) -> Decimal:
        return from_base_units(self.amount_base_units, self.asset.decimals)

    @property
    def collateral_display(self) -> Decimal:
        if self.collateral_asset:
            return from_base_units(self.collateral_amount_base_units, self.collateral_asset.decimals)
        return Decimal(0)

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return self.status == ObligationStatus.PENDING and timezone.now() > self.due_at

    def is_payable_for(self, account) -> bool:
        return self.debtor_account_id == account.pk

    def is_receivable_for(self, account) -> bool:
        return self.creditor_account_id == account.pk


# ---------------------------------------------------------------------------
# Contract — a stored Lapis smart-contract; validated by the VM in vm/.
# ---------------------------------------------------------------------------

class Contract(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, editable=False, unique=True, db_index=True)
    name = models.CharField(max_length=255, unique=True)
    code = models.TextField(blank=True, help_text="Lapis smart-contract YAML.")
    global_state = models.JSONField(default=dict, blank=True, help_text="Runtime Lapis VM state (status, counters, etc.).")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.code:
            from .vm.loader import loads_contract
            from .vm.compiler import ContractFlowValidator
            from .vm.exceptions import LapisValidationError
            try:
                tree = loads_contract(self.code, fmt="yaml")
                ContractFlowValidator().validate(tree)
            except LapisValidationError as exc:
                raise ValidationError({"code": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Agreement — a bilateral agreement governed by a Contract.
# ---------------------------------------------------------------------------

class Agreement(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    source_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="agreements_as_source",
    )
    target_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="agreements_as_target",
    )
    contract = models.ForeignKey(
        Contract, on_delete=models.PROTECT, null=True, blank=True,
        related_name="agreements", help_text="Lapis contract governing this agreement.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Agreement {self.uuid} ({self.source_account} → {self.target_account})"

    def clean(self):
        if self.source_account_id and self.target_account_id:
            if self.source_account_id == self.target_account_id:
                raise ValidationError("Source and target accounts must differ.")
