"""What somebody pays to be here, and what it buys them.

Four models and one idea: a **plan** names a bundle of entitlements and a size,
a **community** may discount it, a **subscription** attaches a person to one,
and a **charge** is one month of it.

**Money is not modelled here.** A charge records what was billed and whether it
went through; the amount, the currency and the ledger entry all belong to the
metered pipeline in ``toto.quota.charge``, which this app calls exactly as
aralia and texlab do. See :mod:`toto.subscriptions.services` for why a
subscription is billed as a quantity rather than as a price.
"""

from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent

#: The metric every subscription charge is written against. One code, one price
#: on the rate card, for everybody — what differs between two subscribers is the
#: QUANTITY, never the price. See services.settle.
METRIC = "subscription.month"


class SubscriptionState(models.TextChoices):
    ACTIVE = "active", _("Active")
    #: A month could not be paid. Access is unchanged during the grace window.
    ARREARS = "arrears", _("In arrears")
    #: The grace window expired. Paid entitlements are gone; nothing else is.
    LAPSED = "lapsed", _("Lapsed")
    CANCELLED = "cancelled", _("Cancelled")


class ChargeStatus(models.TextChoices):
    DUE = "due", _("Due")
    PAID = "paid", _("Paid")
    FAILED = "failed", _("Could not be paid")
    #: Staff forgave it. Distinct from paid, because no money moved.
    WAIVED = "waived", _("Waived")


# SubscriptionPlan was a TABLE here until 2026-09-02. Plans are read-only
# objects built from plans.yaml now (see plans.py): a plan is a decision about
# the product, not user state, and it lived in the database only for as long as
# the admin was its editor — which made the admin a second authoring path
# beside the seeder, with nothing keeping the two in step.
#
# What stayed in the database is what is genuinely state: which plan a person
# is ON (Subscription.plan_key), which communities may BUY one
# (CommunityPlanOffer), what a community takes off the price
# (CommunityDiscount), and what was actually charged (SubscriptionCharge).


class CommunityDiscount(models.Model):
    """What a community takes off its members' subscription. One number.

    **One percentage per community, on every plan.** It used to be one row per
    (community, plan), which meant a new plan silently arrived at full price
    for communities that had negotiated a discount, and an operator had to
    remember to add a row per plan forever. A community either gives its
    members a break or it does not; which plan they picked is not the
    community's business.

    **Highest wins** across a person's communities — the mirror of the head
    weight this replaced, where lowest won. Both say the same thing: belonging
    to a second community can only help, never hurt, because a rule that made
    joining somewhere expensive would be a rule against joining.

    **Never negative.** The column is unsigned and a check constraint caps it
    at 100, so the worst a bad edit can do is make something free — a discount
    that added to a bill would be a surcharge wearing a discount's name.

    A community still receives nothing and owes nothing. This sets what a
    MEMBER pays; the money goes where all platform money goes. Set from the
    Discounts tab (staff), or the admin.
    """

    community = models.OneToOneField(
        "socialhub.Community", on_delete=models.CASCADE,
        related_name="subscription_discount")
    percent = models.PositiveSmallIntegerField(default=0, help_text=_(
        "0–100. Taken off the billed quantity of ANY plan, for members of "
        "this community. Across several communities the LARGEST wins."))

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(percent__lte=100),
                                   name="discount_percent_at_most_100"),
        ]
        ordering = ["community__name"]
        verbose_name = _("community discount")
        verbose_name_plural = _("community discounts")

    def __str__(self):
        return f"{self.community}: −{self.percent}%"


class CommunityPlanOffer(models.Model):
    """One community that may buy one plan. NO ROWS AT ALL MEANS NOBODY.

    This is the eligibility dial, and it is CLOSED BY DEFAULT — which inverts
    what the PlanAudience table it replaces did. That table's rule was "a plan
    with no audience rows is public"; this one's is "a plan nobody is offered
    is a plan nobody can buy". The table was renamed rather than re-used
    precisely so the old sentence cannot be read onto the new behaviour.

    The consequence is deliberate and has to be operated: a new plan reaches
    nobody until somebody offers it, and the Communities tab paints a plan with
    no ticks as *offered to nobody* rather than leaving an empty column to be
    misread as "public". The seeder offers the default plan to every community
    so a fresh platform is never dark.

    **"Active" means the row exists, and nothing else.** There is no `active`
    flag here — a disabled row and a deleted row would be two ways to say one
    thing — and "an active membership in the community" likewise means only
    that a row exists in `socialhub_person_communities`. There is no status,
    no is_active and no joined/left date on membership anywhere in the suite,
    so the whole weight of the eligibility rule rests on row existence, and
    that is worth writing down rather than implying.

    `plan_key` is a STRING, not a foreign key: plans are not rows to point at.
    A key naming a plan the file no longer defines is simply an offer of
    nothing — it can never make somebody eligible, because eligibility is
    computed by intersecting these keys with the registry.

    What an offer does NOT do: it never touches a subscription that already
    exists. It gates the OFFER — the plans page and the subscribe endpoint —
    and a member who leaves the community keeps the plan they are on, exactly
    as a lapsed discount keeps the plan and changes the price. A rule that
    cancelled subscriptions on membership changes would let a community head
    unsubscribe people by expelling them, which is a power nobody asked to
    create.
    """

    community = models.ForeignKey("socialhub.Community",
                                  on_delete=models.CASCADE,
                                  related_name="plan_offers")
    plan_key = models.SlugField(max_length=64, help_text=_(
        "A plan_key from plans.yaml. Not a foreign key: plans are not rows."))
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["community", "plan_key"],
                name="subscriptions_one_offer_per_community_plan"),
        ]
        indexes = [models.Index(fields=["plan_key"])]
        ordering = ["plan_key", "community__name"]
        verbose_name = _("community plan offer")
        verbose_name_plural = _("community plan offers")

    def __str__(self):
        return f"{self.plan_key} → {self.community}"


class Subscription(models.Model):
    """One row per user, mutated in place.

    Not one row per purchase (which is what delta's does): a person has one
    current plan, and the history that matters is the charges, which are
    immutable and dated. Two rows both claiming to be current is a question
    nobody should have to answer.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="subscription")
    #: The plan_key from plans.yaml. A STRING, because the plan it names is a
    #: read-only object rather than a row — which is what lets the ladder be
    #: re-priced without a migration, and what makes a dangling key possible.
    #: `plan` below answers None for one, and `plan_for` falls back to the
    #: default, so a person whose tier left the file loses access rather than
    #: crashing; their charge rows keep the name they paid under.
    plan_key = models.SlugField(max_length=64, db_index=True)
    state = models.CharField(
        max_length=12, choices=SubscriptionState.choices,
        default=SubscriptionState.ACTIVE)

    started_at = models.DateTimeField(auto_now_add=True)
    #: The day the billing months are counted from. Frozen: it defines the
    #: period label, which is half the idempotency key for every charge.
    anchor_date = models.DateField()
    #: When the first unpaid month failed. Cleared the moment one goes through.
    arrears_since = models.DateTimeField(null=True, blank=True)
    #: Optional end (2026-09-26): past it the row grants nothing, whatever its
    #: state says, and the billing sweep marks it lapsed with the reason.
    expires_at = models.DateTimeField(null=True, blank=True)
    #: Why the sweep lapsed it: expired, withdrawn (no community of theirs
    #: offers the plan any more), not-superuser (a plan for admins on an
    #: account that is not one), overdue (arrears ran out). Blank while active.
    lapse_reason = models.CharField(max_length=20, blank=True)
    #: An operator's grant (`subscribe(force=True)`): the plan was given, not
    #: bought, so no Community need offer it — the one exception to the live
    #: offer check, and it still expires and still needs a superuser for a
    #: plan for admins. Nothing a request can set.
    forced = models.BooleanField(default=False)
    #: Whether the plan is one for administrators (`for_admins: true` in
    #: plans.yaml), stored so the admin can list and filter by it
    #: (2026-09-28). The PLAN decides, never a form: `save()` sets it from
    #: the plan every time, and the field is not editable anywhere. While the
    #: row is live (active or in arrears) it may belong to a Django superuser
    #: only — `clean()` and `save()` both refuse anything else, so neither the
    #: admin nor a script can put an ordinary account on an admin plan.
    for_admins = models.BooleanField(
        _("for admins"), default=False, editable=False, help_text=_(
            "Set from the plan: only Django superusers may hold a plan for "
            "administrators."))
    changed_at = models.DateTimeField(auto_now=True)

    @property
    def plan(self):
        """The registry object, or None if the key left the file."""
        from . import plans

        return plans.get(self.plan_key)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [models.Index(fields=["state"])]
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")

    def __str__(self):
        return f"{self.user} — {self.plan} ({self.state})"

    def _plan_is_for_admins(self) -> bool:
        plan = self.plan
        return bool(plan is not None and plan.admin_only)

    def _refuse_admin_plan_to_ordinary_account(self) -> None:
        """A LIVE row on an admin plan belongs to a superuser, or nobody.

        Only live rows: a holder who stops being a superuser keeps a row
        that grants nothing (`plan_for` ignores it) until the billing sweep
        writes it down as lapsed with the reason ``not-superuser`` — and that
        write, and a cancel, must go through.
        """
        if not (self.for_admins and self.is_paying and self.user_id):
            return
        if getattr(self.user, "is_superuser", False):
            return
        plan = self.plan
        raise ValidationError({"plan_key": _(
            "%(plan)s is for administrators: only a Django superuser may hold it.")
            % {"plan": plan.name if plan is not None else self.plan_key}})

    def clean(self):
        super().clean()
        self.for_admins = self._plan_is_for_admins()
        self._refuse_admin_plan_to_ordinary_account()

    def save(self, *args, **kwargs):
        # The plan decides, on EVERY save — a partial one included, so a
        # plan switch written with update_fields carries the flag with it.
        self.for_admins = self._plan_is_for_admins()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            kwargs["update_fields"] = {*update_fields, "for_admins"}
        self._refuse_admin_plan_to_ordinary_account()
        super().save(*args, **kwargs)

    @property
    def is_paying(self) -> bool:
        """Whether this subscription currently grants what it sells.

        Arrears still grants: the whole point of a grace window is that access
        does not blink out the first time a wallet is empty.
        """
        return self.state in (SubscriptionState.ACTIVE, SubscriptionState.ARREARS)

    def is_expired(self, now=None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or timezone.now())


class SubscriptionCharge(models.Model):
    """One month. Immutable once it lands, and idempotent by its label.

    ``(subscription, period_label)`` is the whole reason a charge can be
    materialised from a page render, a celery beat and a management command
    without any of them knowing about the others.
    """

    subscription = models.ForeignKey(
        Subscription, on_delete=models.CASCADE, related_name="charges")
    #: "YYYY-MM". The period this covers, not the day it was billed.
    period_label = models.CharField(max_length=10)
    #: Snapshotted: a plan's size or a community's discount may change later,
    #: and this row is what was actually charged. The snapshot matters MORE
    #: now that the ladder is a file somebody can edit between two months —
    #: and the receipt carries the identity as well as the label, so a tier
    #: that was renamed or removed is still identifiable on an old invoice.
    plan_key = models.SlugField(max_length=64, blank=True)
    plan_name = models.CharField(max_length=120, blank=True)
    units = models.DecimalField(max_digits=12, decimal_places=2,
                                default=Decimal("0"))
    discount_percent = models.PositiveSmallIntegerField(default=0)
    #: Which community earned the discount, for the line on the page. Free text
    #: rather than an FK: the community may be deleted, and this is a receipt.
    discount_source = models.CharField(max_length=200, blank=True)

    status = models.CharField(max_length=10, choices=ChargeStatus.choices,
                              default=ChargeStatus.DUE)
    detail = models.CharField(max_length=300, blank=True, help_text=_(
        "Why it failed, in the words the user was shown."))
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_label"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "period_label"],
                                    name="uniq_subscription_charge_period"),
        ]
        indexes = [models.Index(fields=["status"])]
        verbose_name = _("subscription charge")
        verbose_name_plural = _("subscription charges")

    def __str__(self):
        return f"{self.subscription.user} {self.period_label} — {self.status}"

    @property
    def is_settled(self) -> bool:
        return self.status in (ChargeStatus.PAID, ChargeStatus.WAIVED)


# ---------------------------------------------------------------------------
# Metering
# ---------------------------------------------------------------------------
# The standard opt-in (toto.quota.models): each app owns its own pair, so the
# rows live in this app's tables and go away with it. A subscription writes one
# usage event per month, which is what makes it show up on the Records page and
# in `spend_by_metric` beside everything else somebody pays for.

class SubscriptionUsageEvent(AbstractUsageEvent):
    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Subscription usage event"
        verbose_name_plural = "Subscription usage events"


class SubscriptionQuotaPolicy(AbstractQuotaPolicy):
    events = SubscriptionUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Subscription quota policy"
        verbose_name_plural = "Subscription quota policies"


def default_plan():
    """The plan somebody has when they have none.

    NEVER None now. It used to be, on a host whose plan table was unseeded,
    and that state was load-bearing in the wrong direction: `is_entitled`
    read a missing plan as "open everything", so seeding was what turned
    gating on. A validated file always has exactly one default, so the
    fully-open host is no longer reachable — enforcement is decided by
    BUILD_SUBSCRIPTIONS_ENFORCE alone, which is what it always claimed.
    """
    from . import plans

    return plans.default_plan()


def plan_for(user):
    """The plan actually in force for this user — checked live (2026-09-26).

    A lapsed or cancelled subscription resolves to the default plan, not to its
    own: the row records what they chose, this answers what they currently get.
    So does a key the file no longer defines, a row past its `expires_at`, a
    plan no Community of theirs offers any more (`services.is_eligible`, the
    same predicate the purchase passed — leaving the community or withdrawing
    the offer takes the plan away on the next request, not next month), and
    a plan for admins on an account that is not a superuser: **the plan alone
    grants nothing**, and a holder who stops being a superuser is back on the
    default on the next request. Superusers get no plan for free either: they
    hold the admin plan through their own row. Since 2026-09-28 no Community
    need offer it — every superuser may take it from the plans page, and
    `bootstrap_plans` puts every superuser on it.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return default_plan()
    subscription = Subscription.objects.filter(user=user).first()
    if subscription is None or not subscription.is_paying or subscription.is_expired():
        return default_plan()
    plan = subscription.plan
    if plan is None:
        return default_plan()
    if plan.admin_only and not getattr(user, "is_superuser", False):
        return default_plan()
    from .services import is_eligible

    if not plan.is_default and not subscription.forced and not is_eligible(user, plan.key):
        return default_plan()
    return plan


def superuser_plan_active(user) -> bool:
    """Both, never one: a real superuser AND the admin-only plan in force.

    Superuser functionality (the dashboard's "superuser" tiles, views wearing
    `gate.superuser_plan_required`) asks this. `is_superuser` alone is not
    enough on a host with an admin-only plan, and the plan alone is nothing
    (`plan_for` refuses it to anybody else)."""
    if user is None or not getattr(user, "is_superuser", False):
        return False
    from .plans import admin_plan

    if admin_plan() is None:
        return True                     # no admin plan on this ladder: privilege alone
    return bool(plan_for(user).admin_only)


def period_label(day=None) -> str:
    """"YYYY-MM" for a date. The billing period, everywhere."""
    day = day or timezone.now().date()
    return day.strftime("%Y-%m")


def add_months(day, months: int):
    """Calendar months, clamped to the end of a short month.

    Moved here from portfolio's tribute code when that was removed, which had
    itself copied it from a subscriptions app that predated both. It is
    dependency-free on purpose: ``dateutil`` is not a dependency of this wheel.
    """
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days_in = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return day.replace(year=year, month=month,
                       day=min(day.day, days_in[month - 1]))
