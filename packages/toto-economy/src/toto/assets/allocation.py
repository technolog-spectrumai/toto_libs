"""Funding a branch, and taking the funding back.

The whole offline story rests on one fact: a branch's allocation is an
ordinary ledger balance in its own database. Nothing on the billing path ever
asks the master anything, so the master being unreachable is a non-event until
someone wants MORE money.

Accounting, both sides, with no new concepts:

* master: its reserve → a per-branch EXTERNAL position account. The balance of
  that account is what the master has out at that branch.
* branch: an EXTERNAL counterparty → its own local reserve, from which it
  grants and bills.

An allocation is therefore a TRANSFER, never an increase. Units move out of the
master's reserve and into the branch's; there is visibly more money on the
branch and exactly as much in the world. That is what lets the anti-cheat be
pure arithmetic: drawn ≤ allocated, checked by subtraction.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _


def position_account_code(node: str, asset) -> str:
    return f"branch:position:{node}:{asset.unit_name}"


def branch_reserve_code(asset) -> str:
    return f"branch:reserve:{asset.unit_name}"


def _account(code: str, name: str, account_type):
    from .models import LedgerAccount

    account, _ = LedgerAccount.objects.get_or_create(
        code=code, defaults={"name": name, "account_type": account_type,
                             "active": True})
    return account


def position_account(*, node: str, asset):
    """Master side: what this branch is holding of ours."""
    from .models import AccountType

    return _account(position_account_code(node, asset),
                    f"{node} position ({asset.unit_name})",
                    AccountType.EXTERNAL)


def branch_reserve(*, asset):
    """Branch side: the pot this platform grants and bills out of."""
    from .models import AccountType

    return _account(branch_reserve_code(asset),
                    f"Branch reserve ({asset.unit_name})",
                    AccountType.RESERVE)


def external_counterparty(*, asset):
    """Branch side: where the allocation came from, in one account.

    The only account on this platform allowed to run negative. Its balance is
    the branch's standing claim: how much has arrived from the master and not
    gone back. Summed with every other holding, the branch's book is zero.
    """
    from .models import AccountType, LedgerAccount

    account = _account(f"branch:from-master:{asset.unit_name}",
                       f"Received from master ({asset.unit_name})",
                       AccountType.EXTERNAL)
    if not account.allows_negative:
        LedgerAccount.objects.filter(pk=account.pk).update(allows_negative=True)
        account.refresh_from_db()
    return account


def allocate_to_branch(*, node: str, asset, amount: Decimal, reference: str):
    """Master side: put funds out at a branch. Supply is unchanged."""
    from .issuer import require_master
    from .services.assets import transfer_asset

    require_master("allocate funds to a branch")
    if amount <= 0:
        raise ValidationError(_("An allocation must be positive."))
    if asset.reserve_account is None:
        raise ValidationError(
            f"“{asset.unit_name}” has no reserve account to fund from.")

    return transfer_asset(
        asset=asset, sender_account=asset.reserve_account,
        receiver_account=position_account(node=node, asset=asset),
        amount=amount, reference=reference,
        description=f"Allocation to {node}")


def receive_allocation(*, asset, amount: Decimal, reference: str):
    """Branch side: record funds the master put out at us.

    Credits the local reserve against an EXTERNAL counterparty, so the branch's
    books balance on their own while describing one half of a movement whose
    other half lives on the master.
    """
    from .contracts import may_hold
    from .services.assets import transfer_asset

    if not may_hold(asset):
        raise ValidationError(
            f"This platform has never been contracted for "
            f"“{asset.unit_name}”, so it cannot hold a balance in it.")

    return transfer_asset(
        asset=asset, sender_account=external_counterparty(asset=asset),
        receiver_account=branch_reserve(asset=asset), amount=amount,
        reference=reference, description="Allocation received from master")


def return_unspent(*, asset, reference: str):
    """Branch side: hand the remaining allocation back.

    Called when the master reassigns this platform to a different currency.
    Whatever users already hold stays theirs and stays spendable; what goes
    back is the undrawn pot.
    """
    from .models import AssetHolding, from_base_units
    from .services.assets import transfer_asset

    reserve = branch_reserve(asset=asset)
    holding = AssetHolding.objects.filter(asset=asset, account=reserve).first()
    remaining = holding.balance_base_units if holding else 0
    if remaining <= 0:
        return None

    return transfer_asset(
        asset=asset, sender_account=reserve,
        receiver_account=external_counterparty(asset=asset),
        amount=from_base_units(remaining, asset.decimals),
        reference=reference, description="Unspent allocation returned")


def allocated_base_units(*, asset) -> int:
    """How much this branch has ever been given, in base units."""
    from django.db.models import Sum

    from .models import LedgerEntry

    total = (LedgerEntry.objects
             .filter(asset=asset, account=branch_reserve(asset=asset),
                     amount_base_units__gt=0)
             .aggregate(total=Sum("amount_base_units"))["total"])
    return int(total or 0)


def drawn_base_units(*, asset) -> int:
    """How much of it has left the reserve. Never more than allocated."""
    from django.db.models import Sum

    from .models import LedgerEntry

    total = (LedgerEntry.objects
             .filter(asset=asset, account=branch_reserve(asset=asset),
                     amount_base_units__lt=0)
             .aggregate(total=Sum("amount_base_units"))["total"])
    return int(-(total or 0))
