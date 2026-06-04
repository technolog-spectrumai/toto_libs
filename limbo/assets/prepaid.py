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
