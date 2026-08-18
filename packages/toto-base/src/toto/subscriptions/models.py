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


class SubscriptionPlan(models.Model):
    """A bundle of entitlements at a size.

    ``units`` is the quantity charged per month, NOT a price — the price of one
    unit is one number on the rate card that everybody pays. A plan of 0 units
    is free however the rate card is set, which is what makes the free plan a
    real row rather than a special case in every query.

    **The quantity is SIGNED, and the sign is the direction.** Positive means
    the subscriber pays to be here. Negative means the platform pays *them* —
    a stipend — and that is one mechanism, not two: the same plan, the same
    period, the same ledger, read in the other direction.

    That replaced ``socialhub.Station``, which fused three unrelated things into
    one row: an office, an authorisation grant and a payslip. Only the payslip
    was load-bearing, and it was load-bearing for a reason worth writing down:
    the treasury account every tariff and levy credits was, before it existed,
    **never debited by anything**, against a hard-capped supply. A negative
    subscription is the only thing that puts value back, so the currency keeps
    circulating instead of seizing.
    """

    code = models.SlugField(max_length=64, unique=True, help_text=_(
        "Machine name, e.g. 'standard'. Referenced by seeds and never shown."))
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True, help_text=_(
        "One or two sentences. What the entitlement list cannot say by itself."))

    units = models.IntegerField(default=0, help_text=_(
        "Billed quantity per month of the 'subscription.month' metric. NOT a "
        "price: one unit costs whatever the rate card says it costs, the same "
        "for every subscriber. 0 is free on any rate card. NEGATIVE pays the "
        "subscriber instead of charging them — a stipend."))

    entitlements = models.JSONField(default=list, blank=True, help_text=_(
        "Entitlement codes this plan unlocks — see subscriptions/catalogue.py. "
        "Free entitlements need not be listed; they are added to every plan."))

    is_default = models.BooleanField(default=False, help_text=_(
        "The plan somebody has when they have no subscription. Exactly one, and "
        "it should cost 0 — it is what a lapsed subscriber falls back to."))
    active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "units", "name"]
        verbose_name = _("subscription plan")
        verbose_name_plural = _("subscription plans")

    def __str__(self):
        return self.name

    def entitlement_rows(self):
        """What this plan SELLS, as catalogue objects. Paid rows only.

        The free commons and the machinery used to be unioned in, so every
        card repeated a dozen identical rows before saying anything — and the
        one thing a card exists to answer is "what do I get that I don't
        already have". Rendered from the SAME registry the gate reads, so the
        page and the middleware cannot disagree about what is being sold.
        Codes with no declaration are skipped rather than shown as a bare
        string: an entitlement nobody declared cannot be described, and a
        card is not the place to find out. Free codes that leaked into a
        plan's stored list are skipped for the same reason the union went —
        they are not what the plan sells.
        """
        from .catalogue import registry

        codes = set(self.entitlements or [])
        return [e for e in registry.installed()
                if e.code in codes and not e.free]

    def grants(self, code: str) -> bool:
        return code in set(self.entitlements or [])


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
    plan = models.ForeignKey(
        SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    state = models.CharField(
        max_length=12, choices=SubscriptionState.choices,
        default=SubscriptionState.ACTIVE)

    started_at = models.DateTimeField(auto_now_add=True)
    #: The day the billing months are counted from. Frozen: it defines the
    #: period label, which is half the idempotency key for every charge.
    anchor_date = models.DateField()
    #: When the first unpaid month failed. Cleared the moment one goes through.
    arrears_since = models.DateTimeField(null=True, blank=True)
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-changed_at"]
        indexes = [models.Index(fields=["state"])]
        verbose_name = _("subscription")
        verbose_name_plural = _("subscriptions")

    def __str__(self):
        return f"{self.user} — {self.plan} ({self.state})"

    @property
    def is_paying(self) -> bool:
        """Whether this subscription currently grants what it sells.

        Arrears still grants: the whole point of a grace window is that access
        does not blink out the first time a wallet is empty.
        """
        return self.state in (SubscriptionState.ACTIVE, SubscriptionState.ARREARS)


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
    #: and this row is what was actually charged.
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


def default_plan() -> SubscriptionPlan | None:
    """The plan somebody has when they have none. None on an unseeded host."""
    return (SubscriptionPlan.objects.filter(is_default=True, active=True)
            .order_by("order").first())


def plan_for(user) -> SubscriptionPlan | None:
    """The plan actually in force for this user, or None if nothing is seeded.

    A lapsed or cancelled subscription resolves to the default plan, not to its
    own: the row records what they chose, this answers what they currently get.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return default_plan()
    subscription = (Subscription.objects
                    .filter(user=user).select_related("plan").first())
    if subscription is None or not subscription.is_paying:
        return default_plan()
    return subscription.plan


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
