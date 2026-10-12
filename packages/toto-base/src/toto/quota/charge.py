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
same thing to a caller, and both mean free. **No call site should ever write**
``if billing_enabled():`` — the cap must apply everywhere, and the charge
disables itself.

See also :mod:`toto.quota.rates`, which reaches across the same boundary to
*quote* prices for the two staff-facing screens. It is kept separate because
this module is imported by every metered request and that one is not.

**Two things may stand behind this door** (2026-10-11): the ledger's rate
card (``toto.tariffs``) and Discipline (``toto.discipline``), the platform's
one mana, which knows no ledger. A host has one charger:
``settings.QUOTA_CHARGER`` (``"discipline"`` or ``"tariffs"``) names it;
unstated, it is the rate card where that is installed (every host from
before Discipline keeps what it had), else Discipline where that is. A name
that is not an installed charger (``"none"``) charges nothing, which is how a
host leaves charging out of a test run. The call sites are the same either
way: what :func:`price_for` hands back is only ever handed back in.
"""

from __future__ import annotations

from dataclasses import dataclass

DISCIPLINE = "discipline"
TARIFFS = "tariffs"


def charger() -> str:
    """What charges on this host: ``"discipline"``, ``"tariffs"`` or ``""``."""
    from django.apps import apps
    from django.conf import settings

    installed = {TARIFFS: apps.is_installed("toto.tariffs"),
                 DISCIPLINE: apps.is_installed("toto.discipline")}
    named = getattr(settings, "QUOTA_CHARGER", "")
    if named:
        return named if installed.get(named) else ""
    return TARIFFS if installed[TARIFFS] else DISCIPLINE if installed[DISCIPLINE] else ""


def billing_enabled() -> bool:
    return bool(charger())


@dataclass(frozen=True)
class ManaCard:
    """What :func:`price_for` answers where Discipline charges: the app whose
    metrics cost mana. The prices themselves are Discipline's file."""

    app_label: str


def _mana(tariff) -> bool:
    return isinstance(tariff, ManaCard)


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
    which = charger()
    if not which:
        return None
    if which == DISCIPLINE:
        from toto.discipline import services

        return ManaCard(app_label) if services.priced_app(app_label) else None
    from toto.tariffs.charge import get_tariff_for_user

    return get_tariff_for_user(user, app_label)


def check_funds(user, tariff, metric_code: str, quantity, unit: str = "") -> None:
    """Raise :class:`InsufficientFunds` if the user cannot pay for this action."""
    if not tariff:
        return
    if _mana(tariff):
        from toto.discipline import services

        return services.check(user, metric_code, quantity)
    from toto.tariffs.charge import check_user_can_act

    check_user_can_act(user, tariff, metric_code, quantity, unit)


def check_funds_all(user, tariff, charges) -> None:
    """Raise :class:`InsufficientFunds` unless the user can pay for all of
    ``charges`` (``[(metric_code, quantity, unit), ...]``) TOGETHER.

    :func:`check_funds` asks about one charge. An action that meters two
    things drawn from one balance can pass each of those and still be short
    of their sum; this is the check for such an action.
    """
    if not tariff or not charges:
        return
    if _mana(tariff):
        from toto.discipline import services

        return services.check_all(user, charges)
    from toto.tariffs.charge import check_user_can_act_all

    check_user_can_act_all(user, tariff, charges)


def quote(user, tariff, charges, metrics=()):
    """What :func:`charge` would post for ``charges``
    (``[(metric_code, quantity, unit), ...]``), as plain data, or None when
    nothing is priced. Writes nothing.

    Priced by the calculator the check and the charge themselves run, for
    this user, so the amount quoted is the amount charged. ``metrics`` names
    metric codes whose balance is wanted even when nothing is charged. The
    shape is ``toto.tariffs.charge.quote_user``'s: Decimals, ints and strings
    only — no model instance crosses this boundary.
    """
    if not tariff:
        return None
    if _mana(tariff):
        from toto.discipline import services

        lines = [{"metric_code": code, "quantity": quantity,
                  "amount": services.cost(code, quantity), "asset": "mana"}
                 for code, quantity, *_unit in charges]
        return {"charges": lines, "total": sum((line["amount"] for line in lines), 0),
                "balance": services.balance(user), "asset": "mana"}
    from toto.tariffs.charge import quote_user

    return quote_user(user, tariff, charges, metrics=metrics)


def charge(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Debit the user's account. Returns (UsageRecord, LedgerTransaction) or None.

    Where Discipline charges: ``(the mana event, None)``. The door checked
    before it did the work, so a balance that fell short in between gives
    what it has rather than failing work that is already done; pass
    ``idempotency_key`` to make a retried call charge once."""
    if not tariff:
        return None
    if _mana(tariff):
        return _spend_mana(user, metric_code, quantity, unit, strict=False, **kwargs)
    from toto.tariffs.charge import charge_user

    return charge_user(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def check_and_charge(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Check affordability and debit in one step."""
    if not tariff:
        return None
    if _mana(tariff):
        return _spend_mana(user, metric_code, quantity, unit, strict=True, **kwargs)
    from toto.tariffs.charge import check_and_charge as _f

    return _f(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def credit(user, tariff, metric_code: str, quantity, unit: str = "", **kwargs):
    """Pay the user — the opposite direction to :func:`charge`, same door.

    For a **negative-quantity subscription**: a stipend. The treasury pays a
    member for being here instead of the member paying to be here.

    This is deliberately NOT :func:`refund`. A refund reverses a *posted*
    charge and declines a record that is not POSTED or is already reversed —
    it undoes something. A stipend undoes nothing; there was never a debit.

    ``quantity`` is the magnitude to pay, always positive: the caller has
    already read the sign and chosen this function because of it. Returns the
    ledger transaction, or ``None`` when there is no economy on this host or no
    price for the metric — the same two sentinels every function here honours,
    so a call site still needs no guard of its own.

    The payer is the platform treasury. That account is credited by every
    tariff and every levy and
    is otherwise **never debited**, against a capped supply — which is why this
    function exists at all: it is the only thing that puts value back.
    """
    if not tariff:
        return None
    if _mana(tariff):
        from toto.discipline import services

        amount = services.cost(metric_code, quantity)
        if amount <= 0:
            return None
        return services.grant(user, amount, key=kwargs.get("reference", ""),
                              reason=kwargs.get("description", "") or metric_code)
    from toto.tariffs.charge import credit_user

    return credit_user(user, tariff, metric_code, quantity, unit=unit, **kwargs)


def refund(usage_record, *, reference: str = "", description: str = ""):
    """Reverse a posted charge — for work that was paid for and then failed.

    Returns the reversing transaction, or None when there was nothing to undo.
    """
    if not billing_enabled() or usage_record is None:
        return None
    if charger() == DISCIPLINE:
        from toto.discipline import services

        return services.refund(usage_record, reason=description)
    from toto.tariffs.charge import refund_usage_record

    return refund_usage_record(usage_record, reference=reference, description=description)


def refund_for(source_type: str, source_id, metric_code: str = "", *, reason: str = ""):
    """Reverse the live charge against one domain row. None when there was none.

    Re-finds the record rather than taking one, which is what makes refunding
    possible at all from where failures are actually noticed. ``charge()``
    returns a ``tariffs.UsageRecord``, but the place that discovers the work
    failed is usually a celery worker holding nothing but a pk — a different
    process from the request that paid. Nor can the record be stashed on the
    domain row: a real FK to ``toto.tariffs`` would be ``fields.E300`` at import
    time on every host that ships no economy, which is why ``LatexRun`` already
    carries its workflow link as a plain integer.

    Pass ``metric_code`` whenever a row carries more than one metric, or this
    reverses whichever charge happens to be newest.

    Reversal, not deletion: the charge and its refund both stay in the ledger.
    Safe to call twice — ``refund_usage_record`` declines a record that is not
    POSTED or is already reversed.
    """
    if not billing_enabled():
        return None
    if charger() == DISCIPLINE:
        from toto.discipline import services

        return services.refund_for(source_type, source_id, metric_code, reason=reason)
    from toto.tariffs.models import UsageRecord, UsageStatus

    records = UsageRecord.objects.filter(
        source_type=source_type,
        source_id=str(source_id),
        status=UsageStatus.POSTED,
    )
    if metric_code:
        records = records.filter(metric_code=metric_code)

    return refund(records.order_by("-created_at").first(), description=reason)


def _spend_mana(user, metric_code, quantity, unit, *, strict, **kwargs):
    from toto.discipline import services

    event = services.spend(
        user, metric_code, quantity, strict=strict, unit=unit,
        key=kwargs.get("idempotency_key", ""), source_type=kwargs.get("source_type", ""),
        source_id=kwargs.get("source_id", ""), reason=kwargs.get("description", ""))
    return (event, None) if event is not None else None
