"""
Prepaid account helpers.

Every authenticated user automatically gets one personal prepaid LedgerAccount
that serves as their balance for tariff-metered actions (vault uploads,
AI runs, graph queries, VOD streams, etc.).

Convention: code = "user-prepaid-<user.pk>"

The account is created lazily on first use and eagerly via post_save signal
on User.  This module has no module-level imports from domain apps (tariffs,
vault, etc.); :func:`grant_asset` asks toto.tariffs lazily, and only where it
is installed, what the rate card charges in.
"""
from __future__ import annotations

from django.conf import settings
from django.db import transaction


PREPAID_CODE_PREFIX = "user-prepaid-"


def prepaid_code(user_pk: int | str) -> str:
    return f"{PREPAID_CODE_PREFIX}{user_pk}"


def get_or_create_prepaid_account(user):
    """
    Return (account, created).  Safe to call from signals and views.
    Account is always active; never mutates an existing account.
    """
    from toto.assets.models import AccountType, LedgerAccount

    code = prepaid_code(user.pk)
    with transaction.atomic():
        account, created = LedgerAccount.objects.get_or_create(
            code=code,
            defaults={
                "name": f"Prepaid — {getattr(user, 'username', str(user))}",
                "account_type": AccountType.USER,
                "active": True,
                "user": user,
                "metadata": {"prepaid": True, "user_pk": user.pk},
            },
        )
    return account, created


def get_prepaid_account(user):
    """Return the prepaid account or None if it doesn't exist yet."""
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.filter(code=prepaid_code(user.pk)).first()


#: A prepaid account's name once its holder's account is gone (2026-10-01,
#: 37c.21). The account stays — its entries are the ledger's money trail and
#: are sealed — but it was named "Prepaid — <username>" for good.
GONE_HOLDER_NAME = "Prepaid — deleted account"


def forget_holder(user) -> int:
    """Take ``user``'s username out of their prepaid account's name, before
    the account goes: ``erase_user`` (toto.core.erasure) and the housekeeping
    that prunes an application nobody finished (toto.socialhub.applications)
    call this one function, so the two say the same. Returns how many were
    renamed. The code (``user-prepaid-<id>``) stays: rows are found by it."""
    from django.utils import timezone

    from toto.assets.models import LedgerAccount

    return (LedgerAccount.objects.filter(code=prepaid_code(user.pk))
            .exclude(name=GONE_HOLDER_NAME)
            .update(name=GONE_HOLDER_NAME, updated_at=timezone.now()))


def grant_asset():
    """The asset a starting grant is paid in, or None before any is seeded.

    What metered work is CHARGED in — the platform rate card's currency
    (``Tariff.pricing_asset``: the staff charging currency, then
    ``gas_asset()``) — because the grant exists so that a newcomer can pay for
    it. It used to be the settlement asset, which agrees on a default install
    and parts ways exactly where it matters: the rate desk's charging-currency
    switch re-denominates the rate card without touching the settlement row,
    and a host that names its own ``GAS_ASSET`` bills in it while settlement
    falls back to ASR. Either way a newcomer was funded in a currency no price
    asked for, and refused everything.

    A staff SETTLEMENT choice still moves the grant — ``gas_asset()`` honours
    it first. Without ``toto.tariffs`` nothing is priced, and the settlement
    asset is the only answer there is.
    """
    from django.apps import apps

    from toto.assets.services.settlement import settlement_asset

    if apps.is_installed("toto.tariffs"):
        from toto.tariffs.models import Tariff
        from toto.tariffs.rate_card import DEFAULT_TARIFF_CODE, gas_asset

        tariff = Tariff.objects.filter(code=DEFAULT_TARIFF_CODE).first()
        billed = tariff.pricing_asset() if tariff is not None else gas_asset()
        if billed is not None and billed.active:
            return billed
    return settlement_asset()


def grant_starting_gas(user, amount=None):
    """Fund a new account so it can actually do anything.

    Without this every user starts at zero and every priced action is refused,
    which makes a freshly deployed platform look broken rather than metered.
    The grant is the free tier, denominated in gas.

    Idempotent by reference: one grant per user, ever. Returns the transaction,
    or None when there is nothing to do — no gas asset seeded yet, no reserve
    behind it, or this user was already granted.
    """
    from decimal import Decimal, InvalidOperation

    from django.core.exceptions import ValidationError

    from toto.assets.models import Asset, LedgerTransaction
    from toto.assets.services.assets import distribute_asset

    # Denominated in what the rate card charges, not in a ticker from settings
    # and not in the settlement asset — see grant_asset() for why each of those
    # funded newcomers in a currency nothing on the platform asked them for.
    granting = grant_asset()
    if granting is None:
        return None
    unit = granting.unit_name
    try:
        amount = Decimal(str(amount if amount is not None
                             else getattr(settings, "GAS_STARTING_GRANT", "0")))
    except (InvalidOperation, TypeError):
        return None
    if amount <= 0:
        return None

    asset = Asset.objects.filter(unit_name=unit, active=True).first()
    if asset is None or not asset.reserve_account_id:
        return None

    reference = f"gas-grant-{user.pk}"
    if LedgerTransaction.objects.filter(reference=reference).exists():
        return None

    account, _ = get_or_create_prepaid_account(user)
    try:
        return distribute_asset(
            asset=asset,
            recipient_account=account,
            amount=amount,
            reference=reference,
            description=f"Starting gas grant for {getattr(user, 'username', user.pk)}",
        )
    except ValidationError:
        # The reserve is empty — the platform has granted away its whole supply.
        # A user with no gas is a far better outcome than a failed signup.
        return None
