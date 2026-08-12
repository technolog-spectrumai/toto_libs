"""The payroll: the federal treasury pays the platform's offices.

The return half of the loop. Every tariff and every levy on this platform
credits one account — ``platform-usage-fees``, which ``rate_card.upsert_price``
hardwires as the receiving account for every price it writes — and until now
nothing ever debited it. Tokens went in and stopped. With a fixed supply, a
one-time signup grant and no top-up path, that is an economy that runs down and
seizes; this is the path back out.

**One payer, always.** Every station is paid by the federal treasury, including
one whose whole job serves a single community. A community that paid its own
officers would be a community with its own budget and its own payroll, and the
design does not offer that — ``Station.serves`` says who an office works for,
never who pays it.

**A salary that cannot be paid is still owed**, which is the deliberate inverse
of a levy. A levy that cannot collect is written off and no debt exists; an
office that did its work is owed, so a payment the treasury could not fund stays
PENDING and the next run pays it. Replay runs first, before the current period
is materialised, so a treasury that refills clears its arrears before taking on
more.

Nothing here can pay twice: the period row carries a unique constraint, and the
ledger reference is unique in the database, so a double-fired beat returns the
original transaction rather than a second payment.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from toto.quota.api import period_start

from .models import StipendPayment, StipendStatus

logger = logging.getLogger("toto.tax")

#: Offices are paid weekly. Daily would put 52 times the rows in the ledger to
#: say the same thing, and monthly makes a new holder wait too long to see that
#: the office is real.
STIPEND_PERIOD = "weekly"


def period_label(now=None) -> str:
    """``weekly:2026-08-10`` — calendar-aligned, Monday-based.

    Now-relative on purpose: a worker that was down for three weeks pays ONE
    week when it comes back, not three. Nobody is owed for a period the
    platform never ran.
    """
    start = period_start(STIPEND_PERIOD, now or timezone.now())
    return f"{STIPEND_PERIOD}:{start.date().isoformat()}"


def treasury():
    """The one account that pays. The same one every charge credits."""
    from toto.tariffs.rate_card import revenue_account

    return revenue_account()


def _billing_account(user):
    """Where a charge would be taken from — so that is where pay lands.

    Crediting the prepaid account directly (which ``grant_starting_gas`` does)
    would be wrong for anyone who set a priority account: their pay would sit in
    one account while their bills came out of another.
    """
    from toto.tariffs.charge import _get_billing_account

    return _get_billing_account(user)


def _asset():
    from toto.tariffs.rate_card import gas_asset

    return gas_asset()


def materialise(label, *, now=None):
    """Create this period's row for every filled, paid, active office."""
    from decimal import Decimal

    from toto.assets.models import to_base_units
    from toto.socialhub.models import Station

    asset = _asset()
    if asset is None:
        return []

    payer = treasury()
    created = []
    stations = (Station.objects
                .filter(active=True, holder__isnull=False)
                .exclude(stipend=Decimal("0"))
                .select_related("holder__user"))
    for station in stations:
        user = getattr(station.holder, "user", None)
        if user is None:
            # A paid office needs a holder with a login; Station.clean refuses
            # to create one, but an admin can unlink a User afterwards.
            logger.warning("tax: station %s has no payable holder", station.slug)
            continue
        try:
            with transaction.atomic():
                created.append(StipendPayment.objects.create(
                    station=station,
                    period_label=label,
                    holder=user,
                    payer_account=payer,
                    payee_account=_billing_account(user),
                    asset=asset,
                    amount_base_units=to_base_units(station.stipend,
                                                    asset.decimals),
                ))
        except IntegrityError:
            # This period is already materialised for this office — either a
            # concurrent beat won the race, or this run already did it.
            continue
    return created


def pay(payment) -> bool:
    """Pay one row. True when the money moved (now or on an earlier attempt)."""
    from django.core.exceptions import ValidationError

    from toto.assets.models import from_base_units
    from toto.assets.services.assets import transfer_asset

    if payment.status == StipendStatus.PAID:
        return True

    reference = f"stipend-{payment.station_id}-{payment.period_label}"
    description = (
        f"Stipend · {payment.station.name} · {payment.period_label} · "
        f"paid by the federal treasury"
    )
    try:
        tx = transfer_asset(
            asset=payment.asset,
            sender_account=payment.payer_account,
            receiver_account=payment.payee_account,
            amount=from_base_units(payment.amount_base_units,
                                   payment.asset.decimals),
            reference=reference,
            description=description,
            metadata={"kind": "stipend", "station": payment.station.slug,
                      "period": payment.period_label},
        )
    except ValidationError as exc:
        # Almost always an empty treasury. The row stays PENDING and the next
        # run tries again — the office is still owed.
        payment.note = {"last_error": str(exc)}
        payment.save(update_fields=["note", "updated_at"])
        return False

    payment.status = StipendStatus.PAID
    payment.ledger_transaction = tx
    payment.paid_at = timezone.now()
    payment.save(update_fields=["status", "ledger_transaction", "paid_at",
                                "updated_at"])
    return True


def run_payroll(now=None) -> dict:
    """Pay what is owed, then this period. Idempotent, and safe to double-fire.

    Order matters: arrears first. A treasury that ran dry last week and has
    since collected should clear what it owes before taking on another week.
    """
    label = period_label(now)
    summary = {"period": label, "paid": 0, "unfunded": 0, "created": 0}

    owed = (StipendPayment.objects
            .filter(status=StipendStatus.PENDING)
            .select_related("station", "asset", "payer_account",
                            "payee_account")
            .order_by("created_at"))
    for payment in owed:
        if pay(payment):
            summary["paid"] += 1
        else:
            summary["unfunded"] += 1

    created = materialise(label, now=now)
    summary["created"] = len(created)
    for payment in created:
        if payment.status == StipendStatus.PENDING:
            if pay(payment):
                summary["paid"] += 1
            else:
                summary["unfunded"] += 1

    return summary
