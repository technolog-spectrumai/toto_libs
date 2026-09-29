"""Community discounts on mana charges (2026-09-28).

A member's best ``CommunityDiscount`` (``toto.subscriptions.best_discount`` —
highest wins, functional communities only, never a clearance) used to lower only
the subscription's own monthly bill. It now lowers every charge drawn from a
mana pool as well: a forum message, a geocode, a scan, a levy.

**The rate card stays one price for everybody** (``get_tariff_for_user`` says
why). The discount is a step AFTER rating, applied in
``services.calculate_tariff_charge`` — the one function the affordability
check, the posted charge and the refusal sentence all read, so the three can
never disagree about what an action costs this member. What was taken off is
recorded on the charge's metadata: the list amount, the percent, the community.

Rounded DOWN to the asset's base unit, the rule ``billed_units`` follows: a
discount is never worth fractionally less than its percentage says. A 100 %
discount charges nothing; the usage is still recorded and quotas still count.
"""

from __future__ import annotations

from django.apps import apps
from django.db import DatabaseError

#: Never discounted here. The subscription's own bill is discounted once
#: already, by ``subscriptions.services.settle`` (``billed_units``), and it is
#: paid in the settlement currency, not drawn from a pool — named anyway, so a
#: host that ever prices it in a pool asset still cannot discount it twice.
EXEMPT_METRICS = frozenset({"subscription.month"})


def percent_for_account(payer_account_id) -> tuple[int, str]:
    """``(percent, community name)`` for the person who owns this account.

    ``(0, "")`` for an account with no person behind it (the treasury, a
    community's account), a host without ``toto.subscriptions``, or anything
    that goes wrong — a discount must never break a charge, and the safe
    failure is the list price.
    """
    if not payer_account_id or not apps.is_installed("toto.subscriptions"):
        return 0, ""
    from toto.assets.models import LedgerAccount

    try:
        account = (LedgerAccount.objects.select_related("user")
                   .filter(pk=payer_account_id).first())
    except DatabaseError:
        return 0, ""
    user = account.user if account is not None else None
    if user is None:
        return 0, ""
    from toto.subscriptions.services import best_discount

    return best_discount(user)


def mana_asset_ids() -> set:
    """The assets the mana pools are bound to; empty on a host without mana."""
    if not apps.is_installed("toto.mana"):
        return set()
    try:
        from toto.mana.services import pools

        return {pool.asset_id for pool in pools().values()}
    except DatabaseError:
        return set()


def discounted_base_units(amount: int, percent: int) -> int:
    """``amount`` less ``percent`` %, rounded down to a whole base unit."""
    if percent <= 0 or amount <= 0:
        return amount
    if percent >= 100:
        return 0
    return amount * (100 - percent) // 100


def apply(drafts: list, payer_account_id, metric_code: str) -> list:
    """Discount, in place, every draft charged in a mana asset. Returns them.

    Only when there is a payer to ask about: a simulation, a rate-desk preview
    and a credit to a member have none, and quote the list price.
    """
    if not drafts or not payer_account_id or metric_code in EXEMPT_METRICS:
        return drafts
    percent, source = percent_for_account(payer_account_id)
    if percent <= 0:
        return drafts
    pooled = mana_asset_ids()
    for draft in drafts:
        if draft.asset_id not in pooled:
            continue
        listed = draft.amount_base_units
        draft.amount_base_units = discounted_base_units(listed, percent)
        draft.metadata.update({
            "list_amount_base_units": listed,
            "discount_percent": percent,
            "discount_source": source,
        })
    return drafts
