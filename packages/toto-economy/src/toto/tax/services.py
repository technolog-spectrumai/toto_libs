"""The daily levy, as plain functions the task wraps thinly.

Everything money-shaped goes through ``toto.quota.charge`` — the same door
every metered request uses — and everything measured comes from a registered
:class:`~toto.quota.levy.LevyProvider`. The engine's own contribution is the
order of operations, which follows the documented gate idiom (tariff.md):
the quota usage event is written first with a per-day idempotency key, and the
charge runs only when that write actually created a row. A double-fired beat
is therefore free, and a wholly missed day is simply never billed — there is
no backfill, deliberately, because there is no debt.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from toto.quota import levy as levy_registry_mod
from toto.quota import rates
from toto.quota.api import record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
from toto.quota.metrics import policy_model_for, registry as metric_registry

from . import arrears
from .models import TaxRule

logger = logging.getLogger("toto.tax")


class Outcome:
    LEVIED = "levied"                    # charged and posted
    FREE = "free"                        # holds some, but the metric has no price
    NOTHING_HELD = "nothing_held"        # holds none of the resource
    ALREADY = "already"                  # today's event exists; nothing to do
    FAILED = "failed"                    # insufficient balance; arrears touched
    ERROR = "error"                      # unexpected, logged, run continued


@dataclass
class LevySummary:
    metric_code: str
    day: str = ""
    skipped_reason: str = ""
    counts: dict = field(default_factory=dict)

    def add(self, outcome: str) -> None:
        self.counts[outcome] = self.counts.get(outcome, 0) + 1


def idempotency_key(rule: TaxRule, user_id: int, day) -> str:
    return f"tax.{rule.metric_code}:{user_id}:{day.isoformat()}"


def _is_soft_time_limit(exc: Exception) -> bool:
    """celery's SoftTimeLimitExceeded, without a hard celery import."""
    try:
        from celery.exceptions import SoftTimeLimitExceeded
    except ImportError:  # pragma: no cover - celery-less host
        return False
    return isinstance(exc, SoftTimeLimitExceeded)


def run_daily_levy(day=None) -> list[LevySummary]:
    """Levy every active rule. The beat task calls this and nothing else.

    One clock. There used to be a second sweep riding this beat — a demurrage
    on token holdings, collected into its own account — and it is gone: a head
    tax and a fee on savings do the same job, and the head tax is the one that
    can be explained in a sentence.
    """
    summaries = [levy_rule(rule, day=day) for rule in TaxRule.objects.filter(active=True)]
    for summary in summaries:
        logger.info("tax: %s %s — %s", summary.metric_code, summary.day,
                    summary.skipped_reason or summary.counts)

    # The other direction, on the same clock. Isolated so neither half can take
    # the other down, and OFF by default: collecting is safe to switch on by
    # deploy, paying is not.
    if getattr(settings, "STIPEND_PAYOUT", False):
        try:
            from . import payroll

            logger.info("tax: payroll — %s", payroll.run_payroll())
        except Exception as exc:  # noqa: BLE001
            if _is_soft_time_limit(exc):
                raise
            logger.exception("tax: payroll failed; the levy run is unaffected")
    return summaries


def levy_rule(rule: TaxRule, day=None) -> LevySummary:
    """One rule, one day, every holder of the resource."""
    day = day or timezone.localdate()
    summary = LevySummary(metric_code=rule.metric_code, day=day.isoformat())

    metric = metric_registry.get(rule.metric_code)
    provider = levy_registry_mod.registry.get(rule.metric_code)
    if metric is None or provider is None:
        summary.skipped_reason = "no registered metric" if metric is None else "no levy provider"
        logger.warning("tax: skipping %s — %s", rule.metric_code, summary.skipped_reason)
        return summary

    policy_model = policy_model_for(metric.app_label)
    event_model = getattr(policy_model, "events", None)
    if event_model is None:
        # The provider app must own a concrete AbstractUsageEvent pair; the
        # levy refuses to bill what it cannot record.
        summary.skipped_reason = f"{metric.app_label} has no usage-event table"
        logger.warning("tax: skipping %s — %s", rule.metric_code, summary.skipped_reason)
        return summary

    # One read per rule. This gates charging, so a tariff that prices other
    # vault metrics but not this one can never raise out of charge() here —
    # and an unpriced levy still measures, which is the safe rollout default.
    priced = rule.metric_code in rates.rate_card()

    from django.contrib.auth import get_user_model

    samples = list(provider.sample())
    ids = [user_id for user_id, _raw in samples]
    users = get_user_model().objects.in_bulk(ids)

    # No per-user resolution happens here any more. A community's standing used
    # to arrive as an exemption and a free-band override, both prefetched
    # beside this in_bulk; both are gone. What a community's standing is worth
    # is now expressed as the QUANTITY its provider reports — the head tax
    # samples one weighted head per person — so this loop bills whatever it is
    # handed and knows nothing about who anyone is.
    for user_id, raw in samples:
        user = users.get(user_id)
        if user is None:
            continue
        try:
            outcome = levy_user(rule, metric, provider, user, raw, day,
                                priced=priced, event_model=event_model)
        except Exception as exc:  # noqa: BLE001 - one bad row must not stall the levy
            # ... except the worker telling us to stop: swallowing the soft
            # time limit here would keep looping until the hard SIGKILL.
            if _is_soft_time_limit(exc):
                raise
            logger.exception("tax: levy failed for user %s on %s", user_id, rule.metric_code)
            outcome = Outcome.ERROR
        summary.add(outcome)

    # A user who shed EVERYTHING drops out of the sample entirely — but an
    # open case of theirs must still resolve, or the warning event lingers
    # over holdings that no longer exist.
    from .models import ArrearsStatus, TaxArrearsCase

    sampled_ids = {user_id for user_id, _raw in samples}
    lingering = (TaxArrearsCase.objects
                 .filter(rule=rule,
                         status__in=[ArrearsStatus.OPEN, ArrearsStatus.WARNED])
                 .exclude(user_id__in=sampled_ids)
                 .select_related("user"))
    for case in lingering:
        arrears.resolve_case(case.user, rule, reason=arrears.REASON_NOTHING_HELD)
        summary.add(Outcome.NOTHING_HELD)
    return summary


def levy_user(rule, metric, provider, user, raw: int, day, *, priced: bool,
              event_model) -> str:
    """Levy one user for one day. Returns an :class:`Outcome` string."""
    billable = Decimal(raw) / Decimal(provider.raw_per_unit)
    if billable <= 0:
        # Holding nothing costs nothing, and shedding everything is a way out
        # of arrears — the same way paying is.
        arrears.resolve_case(user, rule, reason=arrears.REASON_NOTHING_HELD)
        return Outcome.NOTHING_HELD

    event = record_usage(
        event_model, rule.metric_code, billable, user,
        unit=metric.unit,
        idempotency_key=idempotency_key(rule, user.pk, day),
        source_type="tax.TaxRule", source_id=str(rule.pk),
        metadata={"measured_raw": raw},
    )
    if event is None:
        # Already levied today (or the write failed, which record_usage keeps
        # indistinguishable on purpose) — never charge without the record.
        return Outcome.ALREADY

    if not priced:
        # Nothing charges, so nothing can be owed: an open case must not
        # survive an unpriced interlude with its old deadline ticking.
        arrears.resolve_case(user, rule, reason=arrears.REASON_UNPRICED)
        return Outcome.FREE

    tariff = price_for(user, metric.app_label)
    if tariff is None:
        arrears.resolve_case(user, rule, reason=arrears.REASON_UNPRICED)
        return Outcome.FREE

    try:
        charge(user, tariff, rule.metric_code, billable, unit=metric.unit,
               source_type="tax.TaxRule", source_id=str(rule.pk),
               description=f"{metric.label} levy {day.isoformat()}")
    except InsufficientFunds:
        # The attempt above wrote the FAILED UsageRecord — the written-off
        # day's audit trail. Its exception carries no numbers, so re-derive
        # the shortfall from the read-only check for the warning text.
        shortfall, asset = _shortfall_info(user, tariff, rule.metric_code, billable, metric.unit)
        arrears.open_or_touch_case(
            user, rule, stored_raw=raw, shortfall=shortfall, asset=asset)
        return Outcome.FAILED

    arrears.resolve_case(user, rule, reason=arrears.REASON_PAID)
    return Outcome.LEVIED


def _shortfall_info(user, tariff, metric_code, quantity, unit):
    """How short the user is, in display units, via the read-only check."""
    try:
        check_funds(user, tariff, metric_code, quantity, unit)
    except InsufficientFunds as exc:
        return exc.shortfall_display, exc.asset_name
    return None, ""


def estimate_for_user(user) -> list[dict]:
    """Live per-rule numbers for the /tax/ page: what you hold, what a day costs.

    The measurement is taken now rather than read from last night's event, so
    the page always explains the *next* charge, not the previous one.
    """
    card = rates.rate_card()
    rows = []
    for rule in TaxRule.objects.filter(active=True):
        provider = levy_registry_mod.registry.get(rule.metric_code)
        metric = metric_registry.get(rule.metric_code)
        if provider is None or metric is None:
            continue
        raw = provider.measure(user)
        measured = Decimal(raw) / Decimal(provider.raw_per_unit)
        billable = measured
        price = card.get(rule.metric_code)
        estimate = None
        if price is not None:
            unit_quantity = price["unit_quantity"] or Decimal("1")
            amount = billable * Decimal(price["price_display"]) / Decimal(unit_quantity)
            estimate = {
                "amount": amount.quantize(Decimal(10) ** -price["asset_decimals"]),
                "asset": price["asset"],
            }
        from .models import TaxArrearsCase, ArrearsStatus

        case = (TaxArrearsCase.objects
                .filter(user=user, rule=rule,
                        status__in=[ArrearsStatus.OPEN, ArrearsStatus.WARNED])
                .first())
        rows.append({
            "rule": rule,
            "metric": metric,
            "measured": measured,
            "measured_raw": raw,
            # A provider that isn't byte-shaped renders its own number; the
            # template falls back to filesizeformat when this is None.
            "display": provider.format_raw(raw),
            "billable": billable,
            "price": price,
            "estimate": estimate,
            "case": case,
        })
    return rows
