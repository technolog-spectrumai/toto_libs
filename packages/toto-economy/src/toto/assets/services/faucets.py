"""Paying a faucet's members, one hour at a time.

## The step is an hour, and it is not a setting

Every payout is denominated per hour and every run pays exactly one hour, so
there is no period to choose, no arithmetic to get wrong when somebody changes
it, and no ambiguity about what a stored ``period_label`` means. A faucet that
needs to pay twice as fast doubles its amounts.

## Idempotency, in two layers

An hourly beat gets retried, fires twice, and overlaps itself. Neither of those
may pay anybody twice, and the guard is not a lock:

1. ``FaucetPayout`` carries ``UniqueConstraint(member, period_label)``. Two
   workers racing the same hour collide in the DATABASE — one inserts, the other
   sees the row and stops. A lock held in Python would be a lock one crashed
   worker holds forever.
2. ``LedgerTransaction.reference`` is unique too, and every payout's reference
   names its member and its hour. So even a payout row created by hand cannot
   mint a second transfer for an hour already paid.

This is the retired treasury payroll's shape. That half of it was right.

## One failure is one failure

Members are paid independently, each in its own transaction. A dry reserve, a
member with no account, a currency gone inactive — each fails that member's
payout, records why in words, and leaves everybody else's alone. A run reports
what it did rather than raising, so the beat's own retry does not re-run the
successes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

log = logging.getLogger("toto.assets.faucets")

#: The one and only step. Not configurable — see the module docstring.
PERIOD_PREFIX = "hourly"


def period_label(at=None) -> str:
    """The label for the hour ``at`` falls in, hour-aligned and in UTC.

    UTC because a label is compared against stored ones, and a server that
    changes timezone — or a host in a zone with a half-hour offset — must not be
    able to produce two labels for one hour.
    """
    moment = (at or timezone.now()).astimezone(timezone.utc)
    return f"{PERIOD_PREFIX}:{moment.strftime('%Y-%m-%dT%H')}"


@dataclass
class RunReport:
    """What one hourly run did. Returned, never raised."""

    label: str = ""
    paid: int = 0
    skipped: int = 0
    failed: int = 0
    failures: list = field(default_factory=list)

    def __str__(self):
        return (f"{self.label}: {self.paid} paid, {self.skipped} already done, "
                f"{self.failed} failed")


def due_members(faucet=None):
    """Everybody a run would consider: active members of active faucets.

    A zero rate is included and pays nothing — it is a member who is on the list
    and currently gets nothing, which is a state staff can set on purpose and
    should be able to see a payout history for.
    """
    from toto.assets.models import FaucetMember

    members = (FaucetMember.objects
               .filter(active=True, faucet__active=True,
                       faucet__asset__active=True)
               .select_related("faucet", "faucet__asset", "user"))
    if faucet is not None:
        members = members.filter(faucet=faucet)
    return members


def run_hour(*, at=None, faucet=None) -> RunReport:
    """Pay every due member for one hour. Idempotent for that hour."""
    label = period_label(at)
    report = RunReport(label=label)

    for member in due_members(faucet):
        outcome = pay_member(member, label)
        if outcome == "paid":
            report.paid += 1
        elif outcome == "skipped":
            report.skipped += 1
        else:
            report.failed += 1
            report.failures.append(outcome)
    return report


def pay_member(member, label: str) -> str:
    """Pay one member for one hour. Returns "paid", "skipped", or a reason.

    Never raises. A faucet run must not be taken down by one member's problem —
    that is how a dry reserve stops everybody else being paid.
    """
    from toto.assets.models import FaucetPayout, FaucetPayoutStatus
    from toto.assets.models import to_base_units

    asset = member.faucet.asset
    amount = Decimal(member.amount_per_hour or 0)

    # Claim the hour FIRST, and let the database referee it. Doing the transfer
    # first and recording it afterwards is the version where a crash in between
    # pays somebody and forgets, so the next run pays them again.
    try:
        with transaction.atomic():
            payout = FaucetPayout.objects.create(
                member=member, period_label=label,
                amount_base_units=to_base_units(amount, asset.decimals),
                status=FaucetPayoutStatus.PENDING)
    except IntegrityError:
        return "skipped"                # this hour is already accounted for

    if amount <= 0:
        # On the list, currently paid nothing. Recorded so the member's history
        # shows the hour rather than a gap that looks like a missed run.
        payout.status = FaucetPayoutStatus.PAID
        payout.detail = "no amount set"
        payout.save(update_fields=["status", "detail"])
        return "paid"

    try:
        tx = _transfer(member, asset, amount, label)
    except Exception as exc:                            # noqa: BLE001
        payout.status = FaucetPayoutStatus.FAILED
        payout.detail = str(exc)[:255]
        payout.save(update_fields=["status", "detail"])
        log.warning("faucet: %s not paid for %s — %s", member.user, label, exc)
        return f"{member.user}: {exc}"

    payout.transaction = tx
    payout.status = FaucetPayoutStatus.PAID
    payout.save(update_fields=["transaction", "status"])
    return "paid"


def _transfer(member, asset, amount: Decimal, label: str):
    """The ordinary asset transfer behind a payout: reserve → the member.

    Every successful payout is a normal transaction and shows up wherever any
    other transaction does. There is no faucet-shaped ledger entry.
    """
    from toto.assets.prepaid import get_or_create_prepaid_account
    from toto.assets.services.assets import distribute_asset

    if not asset.reserve_account_id:
        raise ValidationError(
            f"{asset.unit_name} has no reserve account, so there is nothing to "
            "pay out of.")

    account, _created = get_or_create_prepaid_account(member.user)
    return distribute_asset(
        asset=asset,
        recipient_account=account,
        amount=amount,
        # Names the member and the hour, and the column is unique — the second
        # layer of the idempotency described in the module docstring.
        reference=f"faucet-{member.pk}-{label}",
        description=f"{member.faucet.name}: {amount} {asset.unit_name} "
                    f"for {label}",
    )
