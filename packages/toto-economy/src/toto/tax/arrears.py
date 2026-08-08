"""The arrears state machine: failed levy → warning → week → enforcement.

The lifecycle the daily engine drives, one call per user per day:

* charge failed → :func:`open_or_touch_case` (idempotent per day; delivers the
  warning once, and the week runs from that delivery);
* charge succeeded, or the user dropped back under the allowance →
  :func:`resolve_case`;
* case still unpaid past its deadline → :func:`enforce_if_due` sheds holdings
  down to the rule's *current* allowance.

Nothing here creates debt. A case records that days went unpaid, never what
they would have cost — those days are written off. RESOLVED and ENFORCED are
terminal, so the next failure after either starts a fresh case and a fresh
week; the partial unique constraint on the model makes that shape impossible
to get wrong under a double-running beat.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import notices
from .models import ArrearsStatus, EnforcementAction, TaxArrearsCase, TaxEnforcementAction

logger = logging.getLogger("toto.tax")

REASON_PAID = "paid"
REASON_UNDER_ALLOWANCE = "under_allowance"
# The metric lost its price: nothing is being charged, so nothing is owed —
# a case left WARNED across an unpriced interlude would otherwise enforce
# instantly (stale deadline) on the first failure after re-pricing.
REASON_UNPRICED = "unpriced"

ACTIVE_STATUSES = (ArrearsStatus.OPEN, ArrearsStatus.WARNED)


def grace_days() -> int:
    return getattr(settings, "TAX_ARREARS_GRACE_DAYS", 7)


def _trim(value) -> str:
    """A Decimal without trailing zeros and without scientific notation —
    ``normalize()`` would render 10 as ``1E+1`` in a sentence for a person."""
    text = str(value)
    return text.rstrip("0").rstrip(".") if "." in text else text


def _provider_for(rule):
    from toto.quota.levy import registry

    return registry.get(rule.metric_code)


def _provider_consequence(rule) -> str:
    provider = _provider_for(rule)
    return getattr(provider, "consequence_text", "") if provider else ""


def _active_case(user, rule):
    return TaxArrearsCase.objects.filter(
        user=user, rule=rule, status__in=ACTIVE_STATUSES
    ).first()


def open_or_touch_case(user, rule, *, stored_raw: int, allowance_raw: int,
                       shortfall=None, asset: str = "") -> TaxArrearsCase:
    """Record today's failed levy, warning the user if not yet warned.

    Idempotent per calendar day: a re-run touches nothing it already touched.
    ``failed_days`` counts failed *days*, not attempts. The warning is
    attempted whenever the case is unwarned — including on a later day, when
    the first attempt died on an unexpected error; only a delivered (or
    undeliverable-by-design) warning starts the clock.
    """
    now = timezone.now()
    today = timezone.localdate(now)

    with transaction.atomic():
        case = _active_case(user, rule)
        if case is None:
            try:
                with transaction.atomic():
                    case = TaxArrearsCase.objects.create(
                        user=user, rule=rule, opened_at=now,
                        allowance_raw_at_open=allowance_raw,
                    )
            except IntegrityError:
                # A concurrent run won the constraint race; use its case.
                case = _active_case(user, rule)
                if case is None:  # pragma: no cover - constraint just fired
                    raise

        already_touched_today = (
            case.last_failure_at is not None
            and timezone.localdate(case.last_failure_at) == today
        )
        if not already_touched_today:
            case.failed_days += 1
            case.last_failure_at = now
            case.last_stored_raw = stored_raw
            case.last_shortfall_display = shortfall
            case.last_shortfall_asset = asset
            case.save(update_fields=[
                "failed_days", "last_failure_at", "last_stored_raw",
                "last_shortfall_display", "last_shortfall_asset", "updated_at",
            ])

        if case.warned_at is None:
            _deliver_warning(case, rule, shortfall=shortfall, asset=asset, now=now)

    return case


def _deliver_warning(case, rule, *, shortfall, asset, now) -> None:
    """Create the calendar warning and start the week. OPEN survives a crash.

    Three outcomes: an event (normal), None (the user has no Person profile —
    the calendar channel is impossible, not failing, and holding enforcement
    on a missing profile would make storage free forever, so the clock runs
    with ``warning_channel="none"``), or an exception — swallowed and logged,
    the case stays OPEN and unwarned, and tomorrow's failure retries.
    """
    deadline = now + timedelta(days=grace_days())
    try:
        event = notices.create_warning_event(
            case.user,
            deadline=deadline,
            shortfall=shortfall, asset=asset,
            allowance_text=f"{_trim(rule.allowance)} {rule.unit_label}".strip(),
            consequence=_provider_consequence(rule),
        )
    except Exception:  # noqa: BLE001 - warning must not sink the levy run
        logger.exception("tax: could not create warning event for case %s", case.uuid)
        return

    case.warned_at = now
    case.deadline_at = deadline
    case.status = ArrearsStatus.WARNED
    if event is not None:
        case.warning_event_uid = event.pk
        case.warning_channel = "event"
    else:
        case.warning_channel = "none"
        logger.warning(
            "tax: user %s has no community_profile — case %s warned without "
            "a calendar event; the deadline runs regardless",
            case.user_id, case.uuid,
        )
    case.save(update_fields=[
        "warned_at", "deadline_at", "status",
        "warning_event_uid", "warning_channel", "updated_at",
    ])


def resolve_case(user, rule, *, reason: str) -> TaxArrearsCase | None:
    """Close the active case, if any, and take the warning off the calendar."""
    case = _active_case(user, rule)
    if case is None:
        return None
    case.status = ArrearsStatus.RESOLVED
    case.resolved_at = timezone.now()
    case.resolution_reason = reason
    case.save(update_fields=["status", "resolved_at", "resolution_reason", "updated_at"])
    _delete_warning_event(case)
    return case


def _delete_warning_event(case) -> None:
    """A stale "you will lose data" event must not linger. Best-effort."""
    if case.warning_event_uid is None:
        return
    try:
        from toto.events.models import ScheduledEvent

        ScheduledEvent.objects.filter(pk=case.warning_event_uid).delete()
    except Exception:  # noqa: BLE001 - cosmetic cleanup only
        logger.warning("tax: could not delete warning event %s", case.warning_event_uid)


def enforce_if_due(case, provider, *, now=None):
    """Shed the user's holdings if the warned week has run out. Else no-op.

    The target is the rule's *current* allowance — the staff threshold as it
    stands today, per the platform rule. Each shed item commits atomically
    with its audit row inside the provider, so a crash mid-walk resumes
    tomorrow (the case is still WARNED and still past deadline) and simply
    continues toward the target. Only after the walk does the case flip to
    ENFORCED, which is terminal even when the target was not reached — the
    remainder was protected, and re-deleting daily is exactly what the fresh-
    case-fresh-week rule exists to prevent.
    """
    now = now or timezone.now()
    if case.status != ArrearsStatus.WARNED or case.deadline_at is None:
        return None
    if now < case.deadline_at:
        return None

    rule = case.rule
    target_raw = int(rule.allowance * provider.raw_per_unit)

    def on_deleted(info):
        TaxEnforcementAction.objects.create(
            case=case, action=EnforcementAction.DELETED,
            item_pk=info["pk"], item_label=info.get("title", ""),
            item_key=info.get("key", ""), container=info.get("bucket", ""),
            size_raw=info.get("size", 0),
        )

    def on_skipped(info):
        TaxEnforcementAction.objects.create(
            case=case, action=EnforcementAction.SKIPPED_PROTECTED,
            item_pk=info["pk"], item_label=info.get("title", ""),
            item_key=info.get("key", ""), container=info.get("bucket", ""),
            size_raw=info.get("size", 0),
        )

    result = provider.enforce(case.user, target_raw,
                              on_deleted=on_deleted, on_skipped=on_skipped)

    case.status = ArrearsStatus.ENFORCED
    case.enforced_at = now
    case.reached_target = result.reached_target
    case.enforcement_summary = {
        "deleted_count": result.deleted_count,
        "deleted_raw": result.deleted_raw,
        "skipped_count": result.skipped_count,
        "final_raw": result.final_raw,
        "target_raw": target_raw,
    }
    case.save(update_fields=[
        "status", "enforced_at", "reached_target", "enforcement_summary", "updated_at",
    ])
    if not result.reached_target:
        logger.warning(
            "tax: enforcement on case %s stopped above target — "
            "%s item(s) protected, %s raw units remain (target %s)",
            case.uuid, result.skipped_count, result.final_raw, target_raw,
        )
    _delete_warning_event(case)

    try:
        freed_text = provider.format_raw(result.deleted_raw) or ""
        summary = ""
        if getattr(provider, "consequence_text", ""):
            # A non-storage levy words its own outcome instead of the
            # deleted-files default.
            summary = (f"{result.deleted_count} item(s) "
                       f"({freed_text or result.deleted_raw}) were shed: "
                       f"{provider.consequence_text}.")
        notices.create_enforcement_event(
            case.user,
            deleted_count=result.deleted_count,
            deleted_raw=result.deleted_raw,
            freed_text=freed_text,
            summary=summary,
        )
    except Exception:  # noqa: BLE001 - the deed is done; only the notice failed
        logger.exception("tax: could not create enforcement notice for case %s", case.uuid)

    return result
