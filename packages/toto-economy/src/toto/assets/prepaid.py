"""
Prepaid account helpers.

Every authenticated user automatically gets one personal prepaid LedgerAccount
that serves as their balance for tariff-metered actions (vault uploads,
AI runs, graph queries, VOD streams, etc.).

Convention: code = "user-prepaid-<user.pk>"

The account is created lazily on first use and eagerly via post_save signal
on User.  This module has no imports from domain apps (tariffs, vault, etc.).
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

    # The platform's settlement asset, not a ticker from settings: a starting
    # grant is an internal payment like any other, and it must be denominated in
    # whatever the platform actually pays in. Reading GAS_ASSET here meant a
    # host that had switched settlement asset still granted newcomers the old
    # one — in a currency nothing else on the platform used.
    from toto.assets.services.settlement import settlement_asset

    settling = settlement_asset()
    if settling is None:
        return None
    unit = settling.unit_name
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
