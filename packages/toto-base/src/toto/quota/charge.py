"""Billing gateway — the only place library apps may reach for money.

``toto.quota`` ships in a wheel to every host. The ledger it bills against,
``toto.assets``, and the rate card on top of it, ``toto.tariffs``, are owned by
one host. The library therefore must not import either; it asks the app
registry and degrades to nothing when they are absent. That keeps a host with
no economy — aurelian — booting and running every metered endpoint for free.

Metered apps import from here and nowhere else::

    from toto.quota.charge import InsufficientFunds, price_for, check_funds, charge

    tariff = price_for(request.user, "texlab")
    try:
        check_funds(request.user, tariff, "texlab.compile", 1)
    except InsufficientFunds as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)
    run = do_the_work()
    charge(request.user, tariff, "texlab.compile", 1,
           source_type="texlab.CompileRun", source_id=str(run.pk))

Two sentinels carry the whole "billing may not exist" story, so call sites need
no guard of their own: :func:`price_for` returns ``None`` when tariffs is not
installed *or* when the metric simply has no price, and every other function is
a no-op on a ``None`` tariff. An unpriced metric and an unbilled host are the
same thing to a caller, and both mean free.
"""

from __future__ import annotations


def billing_enabled() -> bool:
    from django.apps import apps

    return apps.is_installed("toto.tariffs")


# Note the two guards do different jobs. This import succeeds whenever the host
# *ships* tariffs, even with the app switched off — the module is on the path
# either way. What decides whether anyone is actually charged is
# billing_enabled() above, which asks the app registry. The stub below is for
# hosts that do not carry the code at all.
try:  # pragma: no cover - depends on what the host ships
    from toto.tariffs.charge import InsufficientBalanceError as InsufficientFunds
except ImportError:
    class InsufficientFunds(Exception):  # type: ignore[no-redef]
        """Stub for hosts without billing — never raised there.

        Field-compatible with the real one so ``except InsufficientFunds`` and
        the attribute reads around it are safe to write unconditionally.
        """

        #: Payment Required. Distinct from quota's 429: the caller is allowed,
        #: they just cannot pay.
        status_code = 402
        asset_name: str = ""
        needed_display = 0
        have_display = 0
        shortfall_display = 0
        topup_url: str = ""


def price_for(user, app_label: str):
    """The rate card governing this user and app, or None when nothing is priced."""
    if not billing_enabled():
        return None
    from toto.tariffs.charge import get_tariff_for_user

    return get_tariff_for_user(user, app_label)


def check_funds(user, tariff, metric_code: str, quantity, unit: str = "") -> None:
    """Raise :class:`InsufficientFunds` if the user cannot pay for this action."""
    if not tariff:
        return
    from toto.tariffs.charge import check_user_can_act

    check_user_can_act(user, tariff, metric_code, quantity, unit)


def charge(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Debit the user's account. Returns (UsageRecord, LedgerTransaction) or None."""
    if not tariff:
        return None
    from toto.tariffs.charge import charge_user

    return charge_user(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def check_and_charge(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Check affordability and debit in one step."""
    if not tariff:
        return None
    from toto.tariffs.charge import check_and_charge as _f

    return _f(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def refund(usage_record, *, reference: str = "", description: str = ""):
    """Reverse a posted charge — for work that was paid for and then failed.

    Returns the reversing transaction, or None when there was nothing to undo.
    """
    if not billing_enabled() or usage_record is None:
        return None
    from toto.tariffs.charge import refund_usage_record

    return refund_usage_record(usage_record, reference=reference, description=description)
