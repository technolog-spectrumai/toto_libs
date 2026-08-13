"""Reading a levy and arming it, from the limits side.

Third of the family. :mod:`toto.quota.rates` quotes and sets *prices* without
importing ``toto.tariffs``; :mod:`toto.quota.times` reads *time dials* without
importing ``toto.tax``; this one reads a levy and arms it — the one knob
``toto.tax`` owns — under the same rule, for the same reason.

The rule: ``toto.quota`` ships in a wheel to every host, and ``toto.tax`` ships
in ``toto-economy``, which several hosts do not pin at all. There it is not
merely uninstalled, it is not importable, so nothing here may name it outside a
function body. Every function degrades to an empty answer rather than raising,
so a caller never needs a guard::

    levy = levies.of("storage.gb_day")     # None on a host with no levy engine
    levies.set_armed("storage.gb_day", True)   # False, changed nothing

**Only plain data crosses the boundary.** No ``TaxRule``, no ``LevyProvider``,
no ``TaxArrearsCase`` instance is ever returned — a quota template that could
reach ``row.rule.metadata`` would blow up on exactly the hosts this indirection
exists to protect.

Why this exists at all: before the per-thing pages, configuring one levy took
two screens in two apps and *neither was sufficient alone* — the rule on
``tax:rules``, the price on ``quota:rate_desk``. Fill in one and you get a levy
that looks armed and charges nothing, with the only warning printed by
``ingress_tax`` at deploy time. Both knobs now sit on the metered thing they
belong to, which is what this module makes possible.
"""

from __future__ import annotations

from django.apps import apps
from django.db import DatabaseError


def levy_enabled() -> bool:
    """True when this host runs a levy engine at all."""
    # is_installed BEFORE any import. A host can pin the economy wheel without
    # installing toto.tax — placidia does — and importing its models there
    # raises RuntimeError out of Django's model metaclass, which
    # `except ImportError` never catches. Every function below repeats this.
    return apps.is_installed("toto.tax")


def levy_codes() -> set[str]:
    """Metric codes that are charged as a recurring levy rather than per action.

    Read from the PROVIDER registry, which lives in toto-base and is populated
    on every host — so this answer is right even where the engine that bills it
    is absent. That matters: a metered thing is still "the kind of thing that is
    levied" on a host with no economy, and the page should say so.
    """
    from .levy import registry

    return {p.metric_code for p in registry.all() if p.metric_code}


def of(metric_code: str) -> dict | None:
    """One levy as plain data, or None when this metric is not levied.

    Present even where ``toto.tax`` is absent — the provider half of a levy
    ships with the app that owns the resource. ``active`` is then None rather
    than False, and the difference is the point: "no engine to bill it" is not
    the same statement as "armed and switched off".
    """
    from .levy import registry

    provider = registry.get(metric_code)
    if provider is None:
        return None

    row = {
        "code": provider.code,
        "metric_code": provider.metric_code,
        "raw_per_unit": provider.raw_per_unit,
        # What falling behind means for THIS resource, in the provider's own
        # words. Empty means the engine's default wording. Read from the
        # provider rather than hardcoded because they genuinely differ — and
        # tax/my_levies.html once hardcoded the storage sentence onto every
        # row, so a time.hold arrears case told people their files would be
        # deleted.
        "consequence": provider.consequence_text or "",
        "unit_label": "",
        "active": None,
        "has_rule": False,
    }
    if not levy_enabled():
        return row

    try:
        from toto.tax.models import TaxRule
    except ImportError:  # pragma: no cover - app installed, wheel absent
        return row
    try:
        rule = (TaxRule.objects
                .filter(metric_code=metric_code)
                .values("unit_label", "active")
                .first())
    except DatabaseError:
        # Mid-migrate, scratch shell, fresh deploy between migrate and seed.
        # The contract is an empty answer, not an exception on a staff page.
        return row
    if rule is not None:
        row.update({
            "unit_label": rule["unit_label"],
            "active": rule["active"],
            "has_rule": True,
        })
    return row


class UnpricedLevy(ValueError):
    """Refusal to arm a levy that would measure nightly and charge nothing."""


def set_armed(metric_code: str, active: bool) -> bool:
    """Arm or disarm one levy. False when nothing was written.

    **Arming requires a price.** A rule that is active and unpriced measures
    every user every night, writes a usage event, and bills zero — armed to all
    appearances and earning nothing. It was reachable because the rule and the
    price lived on two screens in two apps and neither was sufficient alone; now
    they are one form, and this is the guard that keeps them one decision. The
    price must already be set when this is called, which is why the caller
    writes it first.

    A deploy still cannot start a recurring charge: ``ingress_tariffs`` leaves
    the levy metrics out of its seed on purpose and ``ingress_tax`` seeds every
    rule unarmed, so arming remains something a person does. This only refuses
    doing it *badly*.
    """
    if not levy_enabled() or metric_code not in levy_codes():
        return False

    if active:
        from . import rates

        if metric_code not in rates.rate_card():
            raise UnpricedLevy(
                "A levy with no price measures every night and charges nothing. "
                "Set a price, or leave the rule unarmed."
            )

    try:
        from toto.tax.models import TaxRule
    except ImportError:  # pragma: no cover
        return False

    rule, _created = TaxRule.objects.get_or_create(metric_code=metric_code)
    rule.active = bool(active)
    rule.save()
    return True


def my_levy(metric_code: str, user) -> dict | None:
    """This user's live position on one levy: held, free, billable, cost tonight.

    Measured NOW rather than read off last night's usage event, so the page
    explains the *next* charge rather than the previous one — the rule
    ``tax.services.estimate_for_user`` already follows and the reason the two
    agree.
    """
    if not levy_enabled() or user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        from toto.tax import services
    except ImportError:  # pragma: no cover
        return None
    try:
        rows = services.estimate_for_user(user) or []
    except Exception:  # noqa: BLE001 - an estimate must never break the page
        return None

    for row in rows:
        if row["metric"].code != metric_code:
            continue
        case = row.get("case")
        return {
            "measured": row["measured"],
            "display": row["display"],
            "billable": row["billable"],
            "estimate": row["estimate"],
            # The resolved, per-user value from the estimator — a community
            # rate where one applies — not the platform rule's field.
            "unit_label": row["rule"].unit_label,
            # Flattened: a template holding a TaxArrearsCase is exactly what
            # this module exists to prevent.
            "arrears": None if case is None else {
                "status": case.status,
                "deadline_at": case.deadline_at,
            },
        }
    return None


def unpriced_levies() -> list[str]:
    """Levy metric codes that are armed but have no price — they charge nothing.

    The silent misconfiguration this whole area is prone to: measurement runs
    nightly, records usage, and bills zero. Surfaced as a banner on the metered
    thing rather than as a deploy-time log line nobody reads twice.
    """
    if not levy_enabled():
        return []
    from . import rates

    card = rates.rate_card()
    out = []
    for code in sorted(levy_codes()):
        row = of(code)
        if row is None or not row["has_rule"] or row["active"] is False:
            continue
        if code not in card:
            out.append(code)
    return out


def user_is_frozen(user) -> bool:
    """True when this user is past an arrears deadline and still owes.

    The consequence of not paying, and the whole of it: metered writes refuse
    until it clears. Read through this façade for the usual reason —
    ``toto.quota`` ships to hosts where ``toto.tax`` is not importable — and it
    answers False everywhere the engine is absent, so an unbilled host never
    freezes anybody.

    **Never raises.** Refusing to serve someone because the arrears table was
    briefly unreadable would be a far worse failure than letting a debtor
    upload one more file.
    """
    if not levy_enabled():
        return False
    try:
        from toto.tax import arrears

        return arrears.is_frozen(user)
    except Exception:  # noqa: BLE001 - the free answer, never a raise
        return False


# Nothing about communities crosses here any more, and nothing about people
# either. The per-person side of a levy used to be an exemption and an allowance
# override, both resolved through this module so `toto.tax` never imported
# socialhub. Both are gone: there are no allowances (the free tier is the absence
# of a price, not a per-metric band a friend can spend for you), and a community
# no longer changes what a levy charges at all — what it changes is the price of
# a subscription, which is a different mechanism in a different app.
