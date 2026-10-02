"""Materialise the months, settle them, and work out what a month costs.

## Why a subscription is billed as a QUANTITY

The obvious design is a price per plan and a discount per community. This
platform forbids it, in as many words — ``tariffs/charge.py``:

    There is no per-user or per-community branch, deliberately. Prices do not
    vary by who is asking: one rate card, one treasury, and what varies is the
    QUANTITY a levy reports. A price that changed per payer would put a second,
    thinner rating mechanism beside this one and make "what does this cost" a
    question with no single answer.

There WAS a per-community price branch once; it was dead code from the day it
was written and no community tariff ever applied to anybody. So this app does
what the head tax did before it — moves the variation into the quantity:

    billed units = plan.units × (1 − best community discount)

and the price of one unit is one number on the rate card, the same for
everybody. In exchange the whole money pipeline comes for free: the charging
currency, the 402 on an empty wallet, the ``UsageRecord``/``UsageCharge``
journals, the Records page, ``spend_by_metric``, and the treasury that already
has a registered fee source. There is no second treasury and no new ledger code
in this app at all.

**On a host where nothing is priced, every plan is free.** ``price_for`` returns
None, ``charge`` is a no-op, and a plan still grants exactly what it says it
grants. That is not a degraded mode to apologise for — it is how zenobia runs
today (``TARIFF_SEED_PRICES = False``), and every page here has to read
correctly in it.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from toto.quota.api import record_usage

from .models import (
    METRIC,
    ChargeStatus,
    CommunityDiscount,
    CommunityPlanOffer,
    Subscription,
    SubscriptionCharge,
    SubscriptionState,
    SubscriptionUsageEvent,
    add_months,
    period_label,
)

#: How long an unpaid month may sit before access lapses. Mirrors
#: TAX_ARREARS_GRACE_DAYS, which is the same promise about a different charge.
DEFAULT_GRACE_DAYS = 7

#: A subscription cannot back-bill more than this many months in one pass.
#: An anchor date typo would otherwise mint a decade of charges on one render.
MAX_PERIODS = 36


def grace_days() -> int:
    from django.conf import settings

    return int(getattr(settings, "SUBSCRIPTION_GRACE_DAYS", DEFAULT_GRACE_DAYS))


# ---------------------------------------------------------------------------
# What a month costs
# ---------------------------------------------------------------------------

def best_discount(user, plan=None) -> tuple[int, str]:
    """The largest discount this user's communities give. Plan-agnostic.

    Returns ``(percent, source)`` — the source names the community that earned
    it, because "you pay less because of X" is the sentence that makes a
    discount feel like a membership benefit rather than a pricing accident.

    ``plan`` is accepted and ignored: a discount is one number per community
    on every plan now, and keeping the argument means the quote and the
    settle, which both have a plan in hand, did not have to change.

    Never raises. A database mid-migrate, a user with no profile, a host with no
    socialhub: all of them are "no discount", which is the honest answer and
    also the safe one — it can only ever make somebody pay MORE than the
    optimistic reading, never less than they agreed to.

    A clearance gives no discount (2026-09-28): only the person's functional
    communities are asked, whatever rows exist.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return 0, ""
    try:
        person = getattr(user, "community_profile", None)
        if person is None:
            return 0, ""
        row = (CommunityDiscount.objects
               .filter(community__in=person.communities.all())
               .select_related("community")
               .order_by("-percent")
               .first())
    except Exception:  # noqa: BLE001 - a discount must never break a page
        return 0, ""
    if row is None or row.percent <= 0:
        return 0, ""
    return int(row.percent), row.community.name


def set_discounts(posted) -> tuple[int, int]:
    """Apply a Discounts-tab submission. Returns (saved, cleared).

    Reads ``discount-<community_pk>`` fields. Anything unparseable or out of
    range is IGNORED rather than saved as zero: a typo must not silently
    cancel a community's discount, and the form re-renders showing what is
    actually stored. Zero clears the row — the table is the set of communities
    that give something, not a row per community forever. A clearance's field is
    ignored like an unknown one: the tab lists none, and a posted one is not a
    way to give a clearance a discount.
    """
    from toto.socialhub.models import Community

    valid_pks = set(Community.objects.all().values_list("pk", flat=True))
    saved = cleared = 0
    for key, raw in posted.items():
        if not key.startswith("discount-"):
            continue
        try:
            community_pk = int(key.removeprefix("discount-"))
            percent = int(str(raw).strip())
        except (TypeError, ValueError):
            continue
        if community_pk not in valid_pks or not 0 <= percent <= 100:
            continue
        if percent == 0:
            deleted, _ignored = CommunityDiscount.objects.filter(
                community_id=community_pk).delete()
            cleared += int(bool(deleted))
            continue
        row, created = CommunityDiscount.objects.update_or_create(
            community_id=community_pk, defaults={"percent": percent})
        saved += 1
    return saved, cleared


def _offered_keys(user) -> frozenset:
    """Plan keys offered to at least one community this person belongs to.

    "An active membership" means a row exists in the person's communities M2M,
    because that is all membership IS in this suite — there is no status field
    on it anywhere. Admission is the grant and expulsion is the revocation.
    Functional communities only: a clearance is offered no plan (2026-09-28).
    """
    person = getattr(user, "community_profile", None) \
        if getattr(user, "is_authenticated", False) else None
    if person is None:
        return frozenset()
    return frozenset(
        CommunityPlanOffer.objects
        .filter(community_id__in=_community_ids_with_parents(person))
        .values_list("plan_key", flat=True))


#: How far up a Community tree an offer is looked for. Trees are shallow;
#: this is a guard against a cycle written by hand, not a design limit.
MAX_TREE_DEPTH = 20


def _community_ids_with_parents(person) -> set:
    """The person's functional Communities and every functional ancestor of
    theirs (2026-09-26): an offer made to `toto` reaches a member of
    `toto-dev`. A sub-community can only add offers, never take a parent's
    away. A clearance is on neither end (2026-09-28): not as the person's
    community, and not as an ancestor, which the walk neither counts nor
    climbs through."""
    from toto.socialhub.models import Community

    rows = dict(person.communities.all().values_list("pk", "parent_id"))
    ids = set(rows)
    frontier = {parent for parent in rows.values() if parent}

    depth = 0
    while frontier and depth < MAX_TREE_DEPTH:
        frontier -= ids
        if not frontier:
            break
        rows = dict(Community.objects.all().filter(pk__in=frontier)
                    .values_list("pk", "parent_id"))
        ids |= set(rows)
        frontier = {parent for parent in rows.values() if parent}
        depth += 1
    return ids


def is_eligible(user, plan_key: str) -> bool:
    """May this person buy this plan? CLOSED BY DEFAULT.

    The single predicate, read by the plans page AND by the subscribe
    endpoint AND by `subscribe()` itself — one rule read three times, so an
    early refusal and a late one can never disagree.

    The rule inverts what PlanAudience did: a plan offered to no community is
    offered to NOBODY, rather than to everyone. A plan for admins
    (`for_admins: true`) is the one plan outside that rule, and three
    carve-outs are deliberate:

    * **A plan for admins is every Django superuser's, and nobody else's**
      (2026-09-28). No Community offer is asked for — only admins, and all
      admins. Staff are not admins; an offer reaching an ordinary member
      changes nothing.
    * **Staff and superusers see every other plan.** They administer the
      offers, and a dial you cannot see is a dial you cannot check.
    * **The default plan is always eligible for a signed-in person.** Without
      it, somebody whose communities offer nothing would be shown an empty
      plans page while `plan_for()` still puts them on the free tier — a page
      denying the existence of the plan they are on. Requiring operators to
      offer the free plan to every community is a chore that WILL be forgotten
      on the first new community.
    """
    from . import plans

    plan = plans.get(plan_key)
    if plan is None:
        return False
    if plan.admin_only:
        # Django superusers, every one of them, and nobody else — staff are
        # not admins (1.51). No offer asked for (2026-09-28, which undid the
        # 2026-09-26 "and a Community must offer it"): the owner's rule is
        # "only admins", and a superuser outside every Community is an admin.
        return bool(getattr(user, "is_authenticated", False)
                    and getattr(user, "is_superuser", False))
    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    if plan.is_default:
        return True
    return plan_key in _offered_keys(user)


def eligible_plans(user) -> tuple:
    """Every plan this person may see, and therefore may subscribe to.

    An immutable tuple of registry objects, not a queryset. No `.distinct()`
    is needed any more — membership of two offering communities is one key in
    a set, so the duplicate the old filter had to defend against cannot arise.
    """
    from . import plans

    if getattr(user, "is_superuser", False):
        return plans.all_plans()
    if getattr(user, "is_staff", False):
        return plans.public_plans()
    if not getattr(user, "is_authenticated", False):
        return ()
    offered = _offered_keys(user)
    # Never a plan for admins here, even where a Community of theirs happens
    # to offer one (the operators' Community offers every plan): the card
    # would lead to a 404.
    return tuple(plan for plan in plans.all_plans()
                 if not plan.admin_only and (plan.is_default or plan.key in offered))


def offering_communities(user, plan) -> list:
    """Community names that put this plan within reach of this person.

    What the card's "offered through …" line says. Empty for the default plan
    and for staff seeing a plan nobody offers — the template says so rather
    than implying the plan is public. Never a clearance, which offers nothing.
    The same communities `is_eligible` reads (41.4b): the person's own and
    their ancestors, so an offer `toto` makes names `toto` on the card of a
    `toto-dev` member — it named nobody, while the plan was theirs to buy.
    """
    person = getattr(user, "community_profile", None) \
        if getattr(user, "is_authenticated", False) else None
    if person is None:
        return []
    return sorted(
        CommunityPlanOffer.objects
        .filter(plan_key=plan.key, community_id__in=_community_ids_with_parents(person))
        .values_list("community__name", flat=True))


def set_offers(posted) -> tuple[int, int]:
    """Apply a Communities-tab submission. Returns (added, removed).

    Reads ``aud-<community_pk>-<plan_key>`` checkbox fields against the posted
    ``aud-seen`` list of ``<community_pk>-<plan_key>`` pairs the form rendered.
    Diffing against what was RENDERED rather than against the whole table is
    what makes the form safe to submit from a stale page: a pair the form
    never showed (a plan added since, a community created since) is left
    exactly as it is, never silently cleared because a checkbox for it did
    not arrive.

    The key is split on the FIRST hyphen only. A plan_key may contain one —
    `KEY_RE` allows it — and `split("-")` would have quietly dropped every
    such plan from both sets, which reads as "the operator unticked it".

    A clearance's pair is ignored like an unknown one (2026-09-28): the grid
    renders none, and a forged one neither adds an offer nor removes one.
    """
    from toto.socialhub.models import Community

    from . import plans

    valid_communities = set(Community.objects.all().values_list("pk", flat=True))
    valid_plans = plans.keys()

    def _pair(text):
        community_text, _sep, plan_key = text.partition("-")
        try:
            community_pk = int(community_text)
        except (TypeError, ValueError):
            return None
        if community_pk not in valid_communities or plan_key not in valid_plans:
            return None
        return community_pk, plan_key

    seen = {pair for raw in posted.getlist("aud-seen")
            if (pair := _pair(raw)) is not None}
    ticked = {pair for key in posted
              if key.startswith("aud-") and key != "aud-seen"
              and (pair := _pair(key.removeprefix("aud-"))) is not None}

    added = removed = 0
    for community_pk, plan_key in (ticked & seen):
        _row, created = CommunityPlanOffer.objects.get_or_create(
            plan_key=plan_key, community_id=community_pk)
        added += int(created)
    for community_pk, plan_key in (seen - ticked):
        deleted, _ignored = CommunityPlanOffer.objects.filter(
            plan_key=plan_key, community_id=community_pk).delete()
        removed += int(bool(deleted))
    return added, removed


def billed_units(plan, percent: int) -> Decimal:
    """Plan size after the discount, quantised to two places.

    Rounded DOWN, so a discount is never worth fractionally less than the
    percentage says it is.

    **A discount never applies to a negative plan.** A negative quantity is a
    stipend — the platform paying the subscriber — and "20 % off" a stipend
    would quietly pay somebody less for belonging to a community that was
    supposed to be a benefit. Worse, ROUND_DOWN truncates toward zero, so the
    shrinking would be silent and slightly wrong in the same direction every
    month. A discount reduces what you owe; it has no meaning when you are owed.
    """
    if plan is None or not plan.units:
        return Decimal("0")
    if plan.units < 0:
        return Decimal(plan.units)
    gross = Decimal(plan.units)
    net = gross * (Decimal(100 - int(percent)) / Decimal(100))
    return net.quantize(Decimal("0.01"), rounding="ROUND_DOWN")


def quote(user, plan) -> dict:
    """What this plan would cost this user per month, ready to render.

    ``price`` is None when nothing is priced on this host, which the template
    must say out loud rather than printing a zero — "free" and "we do not bill
    here" look identical in a number and are not the same statement.
    """
    from toto.quota import rates

    percent, source = best_discount(user, plan)
    units = billed_units(plan, percent)
    gross_units = Decimal(plan.units) if plan is not None else Decimal("0")

    row = rates.price_of(METRIC) or {}
    unit_price = row.get("price_display")
    asset = row.get("asset", "")

    return {
        "plan": plan,
        "units": units,
        "gross_units": gross_units,
        "discount_percent": percent,
        "discount_source": source,
        "asset": asset,
        # None means "this host bills nothing", 0 means "this plan is free".
        "price": (unit_price * units) if unit_price is not None else None,
        "gross_price": (unit_price * gross_units) if unit_price is not None else None,
        "priced": unit_price is not None,
    }


# ---------------------------------------------------------------------------
# Subscribing
# ---------------------------------------------------------------------------

class UnapprovedStipend(Exception):
    """A negative plan was assigned with nobody accountable for it."""


class IneligiblePlan(Exception):
    """Somebody tried to take a plan no community of theirs is offered.

    Raised by the service rather than only refused by the view, so the rule
    holds for an API call, an admin action or a management command too.
    """


def subscribe(user, plan, *, approved_by=None, force=False) -> Subscription:
    """Put this user on this plan, from the start of the current month.

    Changing plan does NOT re-bill the month already charged: the
    ``(subscription, period_label)`` constraint means this month is already
    spoken for, and the new size applies from the next one. That is the least
    surprising rule and the only one that cannot be gamed by switching plans
    twice in a month.

    **A NEGATIVE plan needs an approver, and this refuses without one.**

    A negative plan pays its holder every period, out of the treasury, for as
    long as it is attached. Nothing else on the platform creates a recurring
    outbound payment, so this is the only place where somebody could quietly
    arrange to be paid — including arranging it for themselves. The ledger would
    record the money and nothing would record the decision.

    Refusing here rather than in a form or a view is the point: this is the one
    function every path goes through, so a new screen, a management command, a
    fixture or an admin action cannot forget the check. It fails CLOSED, the way
    ``polls`` refuses a formal vote whose roll it cannot resolve.

    ``approved_by`` is a Person who is accountable. From ``toto.jobs`` it is the
    approver on the accepted Offer; until that app exists, an operator seeding a
    position passes one explicitly, and the argument being mandatory is what
    makes the omission visible rather than convenient.

    **Eligibility is re-checked here, for the same reason.** The view refuses
    an ineligible plan with a 404 before it ever gets this far, and this is
    the belt to that braces: an API, an admin action or a management command
    cannot route around the community rule by calling the service directly.

    ``force`` is the ONE bypass, and it is deliberately the only one. This
    guard read ``not force and approved_by is None and not is_eligible(...)``
    for a day, which made naming an approver a second, undocumented way past
    the community rule — and an approver answers a different question. It says
    who is accountable for a recurring outbound payment; it says nothing about
    whether this person may hold this tier. Two doors where the docstring
    above promises one is the failure this line is about.

    So an operator seeding a stipend on a plan no community offers passes BOTH:
    ``approved_by`` because the money needs an owner, ``force`` because the
    eligibility rule is being set aside. Neither is reachable from a request.
    """
    if getattr(plan, "units", 0) < 0 and approved_by is None:
        raise UnapprovedStipend(
            f"Plan {plan.key!r} pays its holder ({plan.units} units per period). "
            "Assigning it needs an accountable approver: pass approved_by, or "
            "create it through an accepted Offer in toto.jobs.")
    if getattr(plan, "admin_only", False) and not getattr(user, "is_superuser", False):
        # Not even `force` puts this plan on an ordinary account: the plan
        # must never grant the privilege (2026-09-26). The model refuses the
        # row too (Subscription.save), for every path that skips this one.
        raise IneligiblePlan(f"{plan.key!r} is for administrators (superusers) only.")
    if not force and not is_eligible(user, plan.key):
        raise IneligiblePlan(
            f"{plan.key!r} is not offered to any community this person is in.")

    today = timezone.now().date()
    subscription = Subscription.objects.filter(user=user).first()
    if subscription is None:
        # Not update_or_create: the anchor must be set on INSERT (the column is
        # NOT NULL) and must NOT be touched on update. Django 4.2 has no
        # create_defaults, so the two cases are written out.
        return Subscription.objects.create(
            user=user, plan_key=plan.key, state=SubscriptionState.ACTIVE,
            anchor_date=today.replace(day=1), forced=bool(force))

    subscription.plan_key = plan.key
    subscription.state = SubscriptionState.ACTIVE
    subscription.arrears_since = None
    subscription.lapse_reason = ""
    subscription.forced = bool(force)
    fields = ["plan_key", "state", "arrears_since", "lapse_reason", "forced", "changed_at"]
    if subscription.anchor_date is None:
        subscription.anchor_date = today.replace(day=1)
        fields.append("anchor_date")
    subscription.save(update_fields=fields)
    return subscription


def cancel(subscription) -> Subscription:
    """Stop billing. Access falls back to the default plan; data is untouched."""
    subscription.state = SubscriptionState.CANCELLED
    subscription.arrears_since = None
    subscription.save(update_fields=["state", "arrears_since", "changed_at"])
    return subscription


# ---------------------------------------------------------------------------
# The months
# ---------------------------------------------------------------------------

def materialize(subscription, *, now=None) -> list[SubscriptionCharge]:
    """Create a charge row for every whole month since the anchor. Idempotent.

    Called from a page render AND from the beat, and neither knows about the
    other — the unique constraint is what makes that safe. A row is created DUE
    and is not settled here: making the row and taking the money are separate so
    a page render never has to touch a wallet.
    """
    now = now or timezone.now()
    today = now.date()
    anchor = subscription.anchor_date
    if anchor is None:
        return []

    made = []
    for n in range(MAX_PERIODS):
        start = add_months(anchor, n)
        if start > today:
            break
        label = period_label(start)
        try:
            with transaction.atomic():
                charge, created = SubscriptionCharge.objects.get_or_create(
                    subscription=subscription,
                    period_label=label,
                    defaults={"plan_key": subscription.plan_key,
                              "plan_name": getattr(subscription.plan, "name", "")},
                )
        except IntegrityError:
            # Another worker won the race; its row is the one that counts.
            continue
        if created:
            made.append(charge)
    return made


def settle(charge, *, user=None) -> SubscriptionCharge:
    """Take the money for one month. Never raises.

    The three outcomes, in the order they are decided:

    * **nothing to charge** — a free plan, or a host with no rate card. Marked
      PAID with zero units, because the month IS settled; leaving it DUE would
      accumulate a backlog of charges nobody owes.
    * **paid** — a real charge through the same door every metered action uses.
    * **failed** — the wallet is short. The month is recorded as failed, the
      subscription enters arrears, and **nothing is taken away today**.
    """
    from toto.quota.charge import InsufficientFunds, charge as spend, price_for

    # Re-read under a lock: settle is reachable from the beat and from a
    # "pay now" button at the same time, and the row's status is the only thing
    # standing between one month and two debits.
    with transaction.atomic():
        locked = (SubscriptionCharge.objects
                  .select_for_update()
                  .filter(pk=charge.pk)
                  .first())
        if locked is None or locked.is_settled:
            return locked or charge
        charge = locked

    subscription = charge.subscription
    user = user or subscription.user
    plan = subscription.plan

    percent, source = best_discount(user, plan)
    units = billed_units(plan, percent)

    charge.plan_key = plan.key
    charge.plan_name = plan.name
    charge.units = units
    charge.discount_percent = percent
    charge.discount_source = source

    tariff = price_for(user, "subscriptions")
    if units == 0 or tariff is None:
        charge.status = ChargeStatus.PAID
        charge.detail = ""
        charge.settled_at = timezone.now()
        charge.save()
        _clear_arrears(subscription)
        return charge

    if units < 0:
        # A stipend: the platform pays the subscriber. Same plan, same period,
        # same ledger — read in the other direction. This is what replaced
        # socialhub.Station (removed 8/2026), and it is the only thing that ever debits the
        # revenue account, so it is what keeps a capped currency circulating.
        #
        # No affordability check: `check_funds` guards the SUBSCRIBER's wallet,
        # and here it is the treasury that must have the money. An empty
        # treasury leaves the charge DUE and the next run tries again — the
        # member is still owed, exactly as payroll has always treated it.
        return _settle_credit(charge, subscription, user, plan, units)

    try:
        # Both halves, the way every metered app does it: the event is this
        # app's own trail (and what the usage bars read), the charge is the
        # money. The event carries the idempotency key — keyed on the PERIOD,
        # not the row — so a retried settle records one month once whatever the
        # row's pk happens to be.
        record_usage(SubscriptionUsageEvent, METRIC, units, user,
                     unit="month",
                     source_type="subscriptions.SubscriptionCharge",
                     source_id=str(charge.pk),
                     source_label=f"{plan.name} {charge.period_label}",
                     idempotency_key=f"subscription:{subscription.pk}:{charge.period_label}")
        spend(user, tariff, METRIC, units,
              source_type="subscriptions.SubscriptionCharge",
              source_id=str(charge.pk),
              description=f"{plan.name} — {charge.period_label}")
    except InsufficientFunds as exc:
        charge.status = ChargeStatus.FAILED
        charge.detail = str(exc)[:300]
        charge.save()
        _enter_arrears(subscription)
        return charge
    except Exception as exc:  # noqa: BLE001 - a billing fault is not a 500 here
        charge.status = ChargeStatus.FAILED
        charge.detail = f"{type(exc).__name__}: {exc}"[:300]
        charge.save()
        _enter_arrears(subscription)
        return charge

    charge.status = ChargeStatus.PAID
    charge.detail = ""
    charge.settled_at = timezone.now()
    charge.save()
    _clear_arrears(subscription)
    return charge


def _settle_credit(charge, subscription, user, plan, units) -> SubscriptionCharge:
    """Pay the subscriber for this period. Never raises.

    The mirror of the paying branch, and deliberately NOT its inverse in every
    respect — two differences are load-bearing:

    * **No arrears.** Arrears mean "you owe us and access is at risk". A member
      the platform could not pay owes nothing; the debt runs the other way. The
      charge stays DUE and the next run retries, which is how ``toto.tax``
      already treats an unpayable stipend.
    * **The usage event is still recorded**, with the same period-keyed
      idempotency key as the paying branch, so the usage bars and this app's
      own trail read one month once — whichever direction the money went.
    """
    from toto.quota.charge import credit

    magnitude = -units
    tariff = None
    try:
        from toto.quota.charge import price_for

        tariff = price_for(user, "subscriptions")
        record_usage(SubscriptionUsageEvent, METRIC, units, user,
                     unit="month",
                     source_type="subscriptions.SubscriptionCharge",
                     source_id=str(charge.pk),
                     source_label=f"{plan.name} {charge.period_label}",
                     idempotency_key=f"subscription:{subscription.pk}:{charge.period_label}")
        credit(user, tariff, METRIC, magnitude,
               unit="month",
               source_type="subscriptions.SubscriptionCharge",
               source_id=str(charge.pk),
               description=f"{plan.name} — {charge.period_label} (stipend)",
               reference=f"subscription-credit:{subscription.pk}:{charge.period_label}")
    except Exception as exc:  # noqa: BLE001 - a billing fault is not a 500 here
        # DUE, not FAILED, and no arrears: nobody defaulted. The treasury is
        # empty or the ledger refused, and the member is still owed.
        charge.detail = f"{type(exc).__name__}: {exc}"[:300]
        charge.save()
        return charge

    charge.status = ChargeStatus.PAID
    charge.detail = ""
    charge.settled_at = timezone.now()
    charge.save()
    _clear_arrears(subscription)
    return charge


def _no_longer_an_admin(subscription) -> bool:
    """A plan for admins on an account that is not a superuser any more.

    Read from the plan, as `Subscription.save` reads it, not from the stored
    flag — the two can only differ after the ladder changed, and the save is
    what would refuse."""
    plan = subscription.plan
    return bool(plan is not None and plan.admin_only
                and not getattr(subscription.user, "is_superuser", False))


def _enter_arrears(subscription) -> None:
    if _no_longer_an_admin(subscription):
        # Arrears would keep the row live, and a live row on a plan for
        # admins is a superuser's or nobody's (Subscription.save refuses it).
        # The row already grants nothing (`plan_for`); write down why.
        _lapse(subscription, "not-superuser")
        return
    fields = ["state", "changed_at"]
    subscription.state = SubscriptionState.ARREARS
    if subscription.arrears_since is None:
        subscription.arrears_since = timezone.now()
        fields.append("arrears_since")
    subscription.save(update_fields=fields)


def _clear_arrears(subscription) -> None:
    if subscription.state == SubscriptionState.ARREARS or subscription.arrears_since:
        if _no_longer_an_admin(subscription):
            # Clearing would make the row live again; see _enter_arrears.
            _lapse(subscription, "not-superuser")
            return
        subscription.state = SubscriptionState.ACTIVE
        subscription.arrears_since = None
        subscription.save(update_fields=["state", "arrears_since", "changed_at"])


def lapse_if_overdue(subscription, *, now=None) -> bool:
    """Drop to the free tier once the grace window has run out.

    **Lapsing changes access and nothing else.** No file is deleted, no row is
    removed, and subscribing again restores everything immediately — the
    charges that failed stay failed and are never back-billed, exactly as a
    missed levy day is written off rather than owed.
    """
    if subscription.state != SubscriptionState.ARREARS or not subscription.arrears_since:
        return False
    now = now or timezone.now()
    if (now - subscription.arrears_since).days < grace_days():
        return False
    _lapse(subscription, "overdue")
    return True


def _lapse(subscription, reason: str) -> None:
    subscription.state = SubscriptionState.LAPSED
    subscription.lapse_reason = reason
    subscription.save(update_fields=["state", "lapse_reason", "changed_at"])


def ineligibility_reason(subscription, *, now=None) -> str:
    """Why a paying row no longer grants its plan, or "" while it does.

    The same facts `plan_for` reads on every request, named so the sweep can
    write them down and the page can say them: `expired`, `withdrawn` (no
    Community of theirs offers the plan), `not-superuser` (a plan for admins
    on an account that is not a superuser, or no longer one), `unknown-plan`
    (the key left the file)."""
    if not subscription.is_paying:
        return ""
    if subscription.is_expired(now):
        return "expired"
    plan = subscription.plan
    if plan is None:
        return "unknown-plan"
    if plan.is_default:
        return ""
    user = subscription.user
    if plan.admin_only and not getattr(user, "is_superuser", False):
        return "not-superuser"
    if not subscription.forced and not is_eligible(user, plan.key):
        return "withdrawn"
    return ""


def lapse_if_ineligible(subscription, *, now=None) -> bool:
    """Write down what `plan_for` already enforces: a paying row that no
    longer grants its plan is lapsed, with the reason (2026-09-26)."""
    reason = ineligibility_reason(subscription, now=now)
    if not reason:
        return False
    _lapse(subscription, reason)
    return True


def sync_for_admins() -> int:
    """Re-read every row's stored `for_admins` flag from the ladder.

    `Subscription.save` sets the flag from the plan, but a row that is not
    saved keeps the value it had — so after a plans.yaml edit (a plan gains
    or loses `for_admins`, the admin plan is renamed or removed) the admin's
    column and filter would answer from the old ladder. Nothing ENFORCES from
    the stored flag (`plan_for`, `is_eligible` and the sweep read the plan),
    so this is bookkeeping: `bootstrap_plans`, which every deploy runs, and
    the billing sweep call it. Two UPDATEs, no save, so no `changed_at`
    moves. Returns how many rows changed.
    """
    from . import plans

    keys = [plan.key for plan in plans.all_plans() if plan.admin_only]
    raised = (Subscription.objects.filter(plan_key__in=keys, for_admins=False)
              .update(for_admins=True))
    lowered = (Subscription.objects.exclude(plan_key__in=keys).filter(for_admins=True)
               .update(for_admins=False))
    return raised + lowered


def run_billing(*, now=None) -> dict:
    """One pass over every subscription: materialise, settle, lapse.

    The beat calls this and nothing else. One subscription failing must not
    stop the run — a wallet problem is per-person by nature, and a sweep that
    stops at the first one silently stops billing everybody after it.
    """
    now = now or timezone.now()
    counts = {"seen": 0, "charged": 0, "failed": 0, "lapsed": 0}
    try:
        sync_for_admins()
    except Exception:  # noqa: BLE001 - bookkeeping; the billing must still run
        pass

    queryset = (Subscription.objects
                .exclude(state=SubscriptionState.CANCELLED)
                .select_related("user"))
    for subscription in queryset.iterator():
        counts["seen"] += 1
        try:
            materialize(subscription, now=now)
            for charge in subscription.charges.filter(
                    status__in=(ChargeStatus.DUE, ChargeStatus.FAILED)):
                settled = settle(charge)
                counts["charged" if settled.is_settled else "failed"] += 1
            if lapse_if_overdue(subscription, now=now) or lapse_if_ineligible(subscription, now=now):
                counts["lapsed"] += 1
        except Exception:  # noqa: BLE001 - one bad row must not end the sweep
            counts["failed"] += 1
    return counts


def usage_series(user, *, days=30):
    """Daily totals of every metric this user was billed for, padded.

    Lives here rather than in quota because it is only wanted by pages that
    already know about subscriptions; quota's own API answers "how much in this
    period", which is a different question from "what did the last month look
    like".
    """
    from datetime import timedelta

    from django.db.models import Sum
    from django.db.models.functions import TruncDate

    since = timezone.now().date() - timedelta(days=days - 1)
    rows = (SubscriptionUsageEvent.objects
            .filter(user=user, occurred_at__date__gte=since)
            .annotate(day=TruncDate("occurred_at"))
            .values("day")
            .annotate(total=Sum("quantity")))
    by_day = {row["day"]: row["total"] for row in rows}
    today = timezone.now().date()
    return [
        {"date": (today - timedelta(days=days - 1 - i)).strftime("%m-%d"),
         "total": float(by_day.get(today - timedelta(days=days - 1 - i), 0) or 0)}
        for i in range(days)
    ]
