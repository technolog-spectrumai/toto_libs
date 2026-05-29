"""
Community treasury account helpers.

Each Community automatically gets one LedgerAccount that serves as its
treasury — it receives tariff revenues, transaction fees, poll tax payments,
subscription income, etc., and pays out payroll.

Convention: code = "community-treasury-<community.pk>"

Created lazily and eagerly via post_save signal on Community.
This module does not import tariffs, budget, or other domain apps.
"""
from __future__ import annotations

from django.db import transaction


TREASURY_CODE_PREFIX = "community-treasury-"


def treasury_code(community_pk: int | str) -> str:
    return f"{TREASURY_CODE_PREFIX}{community_pk}"


def get_or_create_treasury_account(community):
    """
    Return (account, created).  Safe to call from signals and management commands.
    """
    from toto.assets.models import AccountType, LedgerAccount

    code = treasury_code(community.pk)
    with transaction.atomic():
        account, created = LedgerAccount.objects.get_or_create(
            code=code,
            defaults={
                "name": f"Treasury — {community.name}",
                "account_type": AccountType.SYSTEM,
                "active": True,
                "metadata": {
                    "treasury": True,
                    "community_pk": community.pk,
                    "community_name": community.name,
                },
            },
        )
    return account, created


def get_treasury_account(community):
    """Return the treasury account or None if it doesn't exist yet."""
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.filter(code=treasury_code(community.pk)).first()
