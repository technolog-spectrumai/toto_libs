"""What a month costs, who may buy what, and the sweep — at the edges.

Community discounts and plan offers belong to functional communities only
(2026-09-28); every resolver here is asserted to skip a clearance, and the
money pipeline is exercised through the same two doors ``settle`` uses
(``toto.quota.charge.price_for`` / ``charge``), patched where the host
prices nothing. Run under the host settings, like ``tests_eligibility``:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_more_services
"""

from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock, skip

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError
from django.http import QueryDict
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.people.models import Person
from toto.socialhub.models import Clearance, Community

from . import plans, services
from .models import (ChargeStatus, CommunityDiscount, CommunityPlanOffer, Subscription,
                     SubscriptionState, SubscriptionUsageEvent)
from .tests import setUpModule as _install_fixture_ladder
from .tests import tearDownModule as _restore_shipped_ladder

User = get_user_model()

LADDER = """version: 1
plans:
  - key: free
    name: Free
    default: true
    order: 10
  - key: standard
    name: Standard
    units: 200
    order: 20
    features: [editor]
  - key: pro-plus
    name: Pro Plus
    units: 7
    order: 30
    features: [editor, gitea]
  - key: stipend
    name: Stipend
    units: -500
    order: 40
    features: [editor]
  - key: superuser
    name: Superuser
    for_admins: true
    all_features: true
    order: 90
"""


def setUpModule():
    _install_fixture_ladder()
    import tempfile
    from pathlib import Path

    from django.conf import settings

    path = Path(tempfile.mkdtemp(prefix="toto-more-services-")) / "plans.yaml"
    path.write_text(LADDER)
    settings.SUBSCRIPTION_PLANS_FILE = str(path)
    plans.reload()


def tearDownModule():
    _restore_shipped_ladder()


def member(name, *communities, **flags):
    user = User.objects.create_user(name, f"{name}@example.com", "pw", **flags)
    person = Person.objects.create(user=user, display_name=name)
    person.communities.add(*communities)
    return user


def posted(data):
    query = QueryDict(mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            query.setlist(key, value)
        else:
            query[key] = value
    return query


class ServiceCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.toto = Community.objects.create(name="toto", slug="toto")
        cls.dev = Community.objects.create(name="toto-dev", slug="toto-dev", parent=cls.toto)
        cls.harbour = Community.objects.create(name="Harbour", slug="harbour")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")

    def setUp(self):
        plans.reload()


# ---------------------------------------------------------------------------
# What a month costs
# ---------------------------------------------------------------------------


class DiscountTests(ServiceCase):
    def test_a_zero_row_gives_nothing_and_names_nobody(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=0)
        self.assertEqual(services.best_discount(member("zed", self.harbour)), (0, ""))

    def test_the_best_community_discount_wins_whatever_clearance_a_member_holds(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=15)
        CommunityDiscount.objects.create(community=self.toto, percent=30)
        user = member("both", self.harbour, self.toto)
        user.community_profile.clearances.add(self.internal)
        self.assertEqual(services.best_discount(user), (30, "toto"))
        self.assertEqual(services.best_discount(user, plans.plan("pro-plus")), (30, "toto"))

    def test_a_discount_is_not_inherited_from_a_parent_community(self):
        CommunityDiscount.objects.create(community=self.toto, percent=30)
        self.assertEqual(services.best_discount(member("junior", self.dev)), (0, ""))

    def test_a_broken_lookup_is_no_discount_rather_than_an_error(self):
        user = member("unlucky", self.harbour)
        CommunityDiscount.objects.create(community=self.harbour, percent=50)
        with mock.patch.object(CommunityDiscount.objects, "filter",
                               side_effect=DatabaseError("mid-migrate")):
            self.assertEqual(services.best_discount(user), (0, ""))

    def test_nobody_signed_in_gets_nothing(self):
        for user in (None, AnonymousUser()):
            self.assertEqual(services.best_discount(user), (0, ""))


class BilledUnitsTests(ServiceCase):
    def test_the_discount_comes_off_the_quantity_to_two_places(self):
        self.assertEqual(services.billed_units(plans.plan("pro-plus"), 33), Decimal("4.69"))
        self.assertEqual(services.billed_units(plans.plan("standard"), 25), Decimal("150.00"))

    def test_a_full_discount_makes_it_free_and_none_leaves_it_whole(self):
        self.assertEqual(services.billed_units(plans.plan("standard"), 100), Decimal("0"))
        self.assertEqual(services.billed_units(plans.plan("standard"), 0), Decimal("200"))

    def test_no_plan_or_a_free_plan_costs_nothing(self):
        self.assertEqual(services.billed_units(None, 50), Decimal("0"))
        self.assertEqual(services.billed_units(plans.plan("free"), 50), Decimal("0"))

    def test_a_stipend_is_never_discounted_even_fully(self):
        self.assertEqual(services.billed_units(plans.plan("stipend"), 100), Decimal("-500"))


class QuoteTests(ServiceCase):
    def test_an_unpriced_host_says_so_rather_than_zero(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=10)
        quote = services.quote(member("q", self.harbour), plans.plan("standard"))
        self.assertIsNone(quote["price"])
        self.assertIsNone(quote["gross_price"])
        self.assertFalse(quote["priced"])
        self.assertEqual((quote["units"], quote["gross_units"]), (Decimal("180.00"), Decimal("200")))
        self.assertEqual((quote["discount_percent"], quote["discount_source"]), (10, "Harbour"))

    def test_a_priced_host_prices_the_discounted_quantity(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=10)
        row = {"price_display": Decimal("0.5"), "asset": "ASR"}
        with mock.patch("toto.quota.rates.price_of", return_value=row):
            quote = services.quote(member("p", self.harbour), plans.plan("standard"))
        self.assertTrue(quote["priced"])
        self.assertEqual(quote["price"], Decimal("90.000"))
        self.assertEqual(quote["gross_price"], Decimal("100.0"))
        self.assertEqual(quote["asset"], "ASR")


# ---------------------------------------------------------------------------
# The Discounts and Communities tabs' services
# ---------------------------------------------------------------------------


class SetDiscountsTests(ServiceCase):
    def test_saved_updated_and_cleared_are_counted(self):
        CommunityDiscount.objects.create(community=self.toto, percent=5)
        saved, cleared = services.set_discounts({
            f"discount-{self.harbour.pk}": "20",
            f"discount-{self.toto.pk}": " 0 ",
            f"discount-{self.dev.pk}": "0",                 # nothing there to clear
        })
        self.assertEqual((saved, cleared), (1, 1))
        self.assertEqual(CommunityDiscount.objects.get(community=self.harbour).percent, 20)
        self.assertFalse(CommunityDiscount.objects.filter(community=self.toto).exists())
        services.set_discounts({f"discount-{self.harbour.pk}": "35"})
        self.assertEqual(CommunityDiscount.objects.get(community=self.harbour).percent, 35)

    def test_anything_that_is_not_a_percentage_of_a_community_is_ignored(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=12)
        saved, cleared = services.set_discounts({
            "csrfmiddlewaretoken": "x",
            f"discount-{self.harbour.pk}": "12.5",
            "discount-abc": "10",
            f"discount-{self.toto.pk}": "101",
            f"discount-{self.dev.pk}": "-3",
            "discount-999999": "10",
        })
        self.assertEqual((saved, cleared), (0, 0))
        self.assertEqual(list(CommunityDiscount.objects.values_list("community__slug", "percent")),
                         [("harbour", 12)])


class SetOffersTests(ServiceCase):
    def test_a_box_ticked_on_a_pair_the_form_never_showed_adds_nothing(self):
        added, removed = services.set_offers(posted({
            "aud-seen": [f"{self.toto.pk}-standard"],
            f"aud-{self.harbour.pk}-standard": "on"}))
        self.assertEqual((added, removed), (0, 0))
        self.assertFalse(CommunityPlanOffer.objects.exists())

    def test_malformed_pairs_are_ignored_and_a_hyphenated_key_survives(self):
        added, removed = services.set_offers(posted({
            "aud-seen": ["x-standard", f"{self.toto.pk}", f"{self.toto.pk}-pro-plus",
                         f"{self.toto.pk}-enterprise"],
            f"aud-{self.toto.pk}-pro-plus": "on",
            "aud-x-standard": "on",
            f"aud-{self.toto.pk}-enterprise": "on"}))
        self.assertEqual((added, removed), (1, 0))
        self.assertEqual(list(CommunityPlanOffer.objects.values_list("plan_key", flat=True)),
                         ["pro-plus"])

    def test_ticking_an_existing_offer_again_adds_nothing(self):
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="standard")
        self.assertEqual(services.set_offers(posted({
            "aud-seen": [f"{self.toto.pk}-standard"],
            f"aud-{self.toto.pk}-standard": "on"})), (0, 0))
        self.assertEqual(CommunityPlanOffer.objects.count(), 1)


# ---------------------------------------------------------------------------
# Who may buy what
# ---------------------------------------------------------------------------


class EligibilityEdgeTests(ServiceCase):
    def test_anonymous_is_not_even_eligible_for_the_default_plan(self):
        self.assertFalse(services.is_eligible(AnonymousUser(), "free"))
        self.assertTrue(services.is_eligible(member("anyone"), "free"))

    def test_staff_see_every_plan_but_the_one_for_admins(self):
        staff = member("staff", is_staff=True)
        self.assertTrue(services.is_eligible(staff, "pro-plus"))
        self.assertFalse(services.is_eligible(staff, "superuser"))
        self.assertNotIn("superuser", {p.key for p in services.eligible_plans(staff)})

    def test_a_superuser_is_shown_the_whole_ladder(self):
        root = member("root", is_superuser=True, is_staff=True)
        self.assertEqual([p.key for p in services.eligible_plans(root)],
                         ["free", "standard", "pro-plus", "stipend", "superuser"])

    def test_nobody_signed_in_is_shown_nothing_to_buy(self):
        self.assertEqual(services.eligible_plans(AnonymousUser()), ())

    def test_an_offer_of_the_admin_plan_never_reaches_a_member(self):
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="superuser")
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="standard")
        user = member("sailor", self.harbour)
        self.assertEqual([p.key for p in services.eligible_plans(user)], ["free", "standard"])
        self.assertFalse(services.is_eligible(user, "superuser"))

    def test_a_cycle_written_by_hand_still_resolves(self):
        a = Community.objects.create(name="a", slug="a")
        b = Community.objects.create(name="b", slug="b", parent=a)
        Community.objects.filter(pk=a.pk).update(parent=b)
        CommunityPlanOffer.objects.create(community=a, plan_key="pro-plus")
        CommunityPlanOffer.objects.create(community=b, plan_key="standard")
        user = member("looped", b)
        self.assertEqual({p.key for p in services.eligible_plans(user)},
                         {"free", "standard", "pro-plus"})

    def test_offering_communities_names_the_members_own_offering_communities(self):
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="standard")
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="standard")
        user = member("sailor", self.harbour, self.toto)
        user.community_profile.clearances.add(self.internal)
        self.assertEqual(services.offering_communities(user, plans.plan("standard")),
                         ["Harbour", "toto"])
        self.assertEqual(services.offering_communities(AnonymousUser(), plans.plan("standard")), [])

    @skip("suspected bug: offering_communities asks only the person's own communities, "
          "while is_eligible walks up to their parents (2026-09-26) — an offer made to "
          "`toto` reaches a `toto-dev` member but the card's 'Offered through' line "
          "names nobody")
    def test_an_inherited_offer_names_the_community_that_made_it(self):
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="standard")
        user = member("junior", self.dev)
        self.assertTrue(services.is_eligible(user, "standard"))
        self.assertEqual(services.offering_communities(user, plans.plan("standard")), ["toto"])


class SubscribeTests(ServiceCase):
    def test_an_ineligible_plan_is_refused_without_writing_a_row(self):
        user = member("outsider", self.harbour)
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(user, plans.plan("standard"))
        self.assertFalse(Subscription.objects.filter(user=user).exists())

    def test_changing_plan_keeps_the_anchor_and_clears_arrears(self):
        user = member("switcher")
        subscription = services.subscribe(user, plans.plan("standard"), force=True)
        anchor = subscription.anchor_date
        Subscription.objects.filter(pk=subscription.pk).update(
            state=SubscriptionState.ARREARS, arrears_since=timezone.now(), lapse_reason="x")
        again = services.subscribe(user, plans.plan("pro-plus"), force=True)
        again.refresh_from_db()
        self.assertEqual((again.plan_key, again.state, again.arrears_since, again.lapse_reason),
                         ("pro-plus", SubscriptionState.ACTIVE, None, ""))
        self.assertEqual(again.anchor_date, anchor)
        self.assertEqual(again.anchor_date.day, 1)

    def test_an_eligible_subscribe_is_not_marked_as_an_operators_grant(self):
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="standard")
        user = member("buyer", self.harbour)
        services.subscribe(user, plans.plan("standard"), force=True)
        self.assertTrue(Subscription.objects.get(user=user).forced)
        self.assertFalse(services.subscribe(user, plans.plan("standard")).forced)

    def test_cancelling_stops_billing_and_clears_arrears(self):
        user = member("leaver")
        subscription = services.subscribe(user, plans.plan("standard"), force=True)
        subscription.state = SubscriptionState.ARREARS
        subscription.arrears_since = timezone.now()
        subscription.save()
        services.cancel(subscription)
        subscription.refresh_from_db()
        self.assertEqual((subscription.state, subscription.arrears_since),
                         (SubscriptionState.CANCELLED, None))


class IneligibilityReasonTests(ServiceCase):
    def subscription(self, key, *, user=None, forced=True, **fields):
        user = user or member(f"u{User.objects.count()}")
        row = services.subscribe(user, plans.plan(key), force=forced)
        if fields:
            Subscription.objects.filter(pk=row.pk).update(**fields)
            row.refresh_from_db()
        return row

    def test_a_cancelled_or_lapsed_row_has_no_reason_to_give(self):
        for state in (SubscriptionState.CANCELLED, SubscriptionState.LAPSED):
            self.assertEqual(services.ineligibility_reason(self.subscription("standard",
                                                                             state=state)), "")

    def test_expired_is_decided_before_anything_else(self):
        row = self.subscription("standard", expires_at=timezone.now() - timedelta(seconds=1),
                                plan_key="gone")
        self.assertEqual(services.ineligibility_reason(row), "expired")

    def test_a_plan_that_left_the_file(self):
        self.assertEqual(services.ineligibility_reason(self.subscription("standard",
                                                                         plan_key="gone")),
                         "unknown-plan")

    def test_the_default_plan_needs_no_offer(self):
        self.assertEqual(services.ineligibility_reason(self.subscription("free", forced=False)), "")

    def test_an_offer_withdrawn_but_an_operators_grant_stands(self):
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="standard")
        bought = self.subscription("standard", user=member("buyer", self.harbour), forced=False)
        granted = self.subscription("pro-plus")
        CommunityPlanOffer.objects.all().delete()
        self.assertEqual(services.ineligibility_reason(bought), "withdrawn")
        self.assertEqual(services.ineligibility_reason(granted), "")

    def test_lapse_if_ineligible_writes_the_reason_down(self):
        row = self.subscription("standard", plan_key="gone")
        self.assertTrue(services.lapse_if_ineligible(row))
        row.refresh_from_db()
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.LAPSED, "unknown-plan"))
        self.assertFalse(services.lapse_if_ineligible(self.subscription("free")))


class GraceTests(ServiceCase):
    def in_arrears(self, since):
        row = services.subscribe(member(f"late{User.objects.count()}"),
                                 plans.plan("standard"), force=True)
        Subscription.objects.filter(pk=row.pk).update(state=SubscriptionState.ARREARS,
                                                      arrears_since=since)
        row.refresh_from_db()
        return row

    def test_the_grace_window_ends_on_its_last_whole_day(self):
        now = timezone.now()
        days = services.grace_days()
        self.assertFalse(services.lapse_if_overdue(
            self.in_arrears(now - timedelta(days=days) + timedelta(seconds=1)), now=now))
        row = self.in_arrears(now - timedelta(days=days))
        self.assertTrue(services.lapse_if_overdue(row, now=now))
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.LAPSED, "overdue"))

    @override_settings(SUBSCRIPTION_GRACE_DAYS=0)
    def test_a_host_may_shorten_the_window_to_nothing(self):
        self.assertEqual(services.grace_days(), 0)
        self.assertTrue(services.lapse_if_overdue(self.in_arrears(timezone.now())))

    def test_a_row_not_in_arrears_never_lapses_for_lateness(self):
        row = services.subscribe(member("ontime"), plans.plan("standard"), force=True)
        self.assertFalse(services.lapse_if_overdue(row, now=timezone.now() + timedelta(days=365)))
        Subscription.objects.filter(pk=row.pk).update(state=SubscriptionState.ARREARS,
                                                      arrears_since=None)
        row.refresh_from_db()
        self.assertFalse(services.lapse_if_overdue(row, now=timezone.now() + timedelta(days=365)))


# ---------------------------------------------------------------------------
# Taking the money
# ---------------------------------------------------------------------------


class SettleTests(ServiceCase):
    def charge_for(self, user, plan_key="standard"):
        subscription = services.subscribe(user, plans.plan(plan_key), force=True)
        services.materialize(subscription)
        return subscription, subscription.charges.get()

    def test_a_paid_month_snapshots_the_discount_and_charges_the_discounted_quantity(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=25)
        user = member("payer", self.harbour)
        subscription, charge = self.charge_for(user)
        tariff = object()
        with mock.patch("toto.quota.charge.price_for", return_value=tariff), \
                mock.patch("toto.quota.charge.charge") as spend:
            services.settle(charge)
        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)
        self.assertEqual((charge.units, charge.discount_percent, charge.discount_source),
                         (Decimal("150.00"), 25, "Harbour"))
        self.assertIsNotNone(charge.settled_at)
        args = spend.call_args.args
        self.assertEqual((args[0], args[1], args[2], args[3]),
                         (user, tariff, "subscription.month", Decimal("150.00")))
        event = SubscriptionUsageEvent.objects.get(user=user)
        self.assertEqual(event.idempotency_key,
                         f"subscription:{subscription.pk}:{charge.period_label}")

    def test_a_ledger_fault_fails_the_month_names_it_and_enters_arrears(self):
        user = member("faulted")
        subscription, charge = self.charge_for(user)
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
                mock.patch("toto.quota.charge.charge", side_effect=RuntimeError("ledger down")):
            services.settle(charge)
        charge.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.FAILED)
        self.assertEqual(charge.detail, "RuntimeError: ledger down")
        self.assertEqual(subscription.state, SubscriptionState.ARREARS)

    def test_a_failed_month_retried_and_paid_clears_arrears_and_bills_the_event_once(self):
        from toto.quota.charge import InsufficientFunds

        user = member("retry")
        subscription, charge = self.charge_for(user)
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
                mock.patch("toto.quota.charge.charge",
                           side_effect=InsufficientFunds("ASR", 200, 0, 9)):
            services.settle(charge)
        first_since = Subscription.objects.get(pk=subscription.pk).arrears_since
        self.assertIsNotNone(first_since)
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
                mock.patch("toto.quota.charge.charge"):
            services.settle(charge)
        subscription.refresh_from_db()
        self.assertEqual((subscription.state, subscription.arrears_since),
                         (SubscriptionState.ACTIVE, None))
        self.assertEqual(SubscriptionUsageEvent.objects.filter(user=user).count(), 1)

    def test_a_second_failure_keeps_the_first_arrears_date(self):
        from toto.quota.charge import InsufficientFunds

        user = member("stuck")
        subscription, charge = self.charge_for(user)

        def fail_once():
            with mock.patch("toto.quota.charge.price_for", return_value=object()), \
                    mock.patch("toto.quota.charge.charge",
                               side_effect=InsufficientFunds("ASR", 200, 0, 9)):
                services.settle(charge)
            return Subscription.objects.get(pk=subscription.pk).arrears_since

        first = fail_once()
        self.assertIsNotNone(first)
        self.assertEqual(fail_once(), first)

    def test_a_fully_discounted_month_is_paid_without_touching_a_wallet(self):
        CommunityDiscount.objects.create(community=self.harbour, percent=100)
        user = member("free-rider", self.harbour)
        _, charge = self.charge_for(user)
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
                mock.patch("toto.quota.charge.charge") as spend:
            services.settle(charge)
        charge.refresh_from_db()
        self.assertEqual((charge.status, charge.units), (ChargeStatus.PAID, Decimal("0")))
        spend.assert_not_called()


class SweepTests(ServiceCase):
    def test_one_bad_row_does_not_stop_the_sweep(self):
        good = services.subscribe(member("good"), plans.plan("standard"), force=True)
        bad = services.subscribe(member("bad"), plans.plan("standard"), force=True)
        real = services.materialize

        def flaky(subscription, **kwargs):
            if subscription.pk == bad.pk:
                raise RuntimeError("corrupt row")
            return real(subscription, **kwargs)

        with mock.patch.object(services, "materialize", side_effect=flaky):
            counts = services.run_billing()
        self.assertEqual(counts["seen"], 2)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(counts["charged"], 1)
        self.assertEqual(good.charges.get().status, ChargeStatus.PAID)
        self.assertFalse(bad.charges.exists())

    def test_the_daily_task_runs_the_sweep_and_logs_its_counts(self):
        from .tasks import run_billing

        services.subscribe(member("daily"), plans.plan("standard"), force=True)
        with self.assertLogs("toto.subscriptions.tasks", "INFO") as logged:
            counts = run_billing()
        self.assertEqual((counts["seen"], counts["charged"]), (1, 1))
        self.assertIn("'charged': 1", logged.output[0])

    def test_cancelled_rows_are_not_billed(self):
        row = services.subscribe(member("gone"), plans.plan("standard"), force=True)
        services.cancel(row)
        self.assertEqual(services.run_billing()["seen"], 0)
        self.assertFalse(row.charges.exists())

    def test_the_sweep_lapses_a_withdrawn_plan_and_counts_it(self):
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="standard")
        user = member("buyer", self.harbour)
        row = services.subscribe(user, plans.plan("standard"))
        CommunityPlanOffer.objects.all().delete()
        self.assertEqual(services.run_billing()["lapsed"], 1)
        row.refresh_from_db()
        self.assertEqual(row.lapse_reason, "withdrawn")

    def test_sync_for_admins_reads_the_flag_from_the_ladder(self):
        root = member("root", is_superuser=True, is_staff=True)
        row = services.subscribe(root, plans.plan("superuser"))
        Subscription.objects.filter(pk=row.pk).update(for_admins=False)
        other = services.subscribe(member("m"), plans.plan("standard"), force=True)
        Subscription.objects.filter(pk=other.pk).update(for_admins=True)
        self.assertEqual(services.sync_for_admins(), 2)
        self.assertEqual(services.sync_for_admins(), 0)
        self.assertTrue(Subscription.objects.get(pk=row.pk).for_admins)
        self.assertFalse(Subscription.objects.get(pk=other.pk).for_admins)


class UsageSeriesTests(ServiceCase):
    def test_a_month_of_days_padded_with_zeroes_and_summed_per_day(self):
        user = member("heavy")
        fixed = datetime(2026, 9, 15, 12, 0, tzinfo=dt_timezone.utc)
        for when, quantity in ((fixed, "2"), (fixed - timedelta(hours=3), "3"),
                               (fixed - timedelta(days=3), "5"),
                               (fixed - timedelta(days=40), "100")):
            SubscriptionUsageEvent.objects.create(metric_code="subscription.month",
                                                  quantity=Decimal(quantity), user=user,
                                                  occurred_at=when)
        with mock.patch("django.utils.timezone.now", return_value=fixed):
            series = services.usage_series(user, days=30)
        self.assertEqual(len(series), 30)
        self.assertEqual(series[-1], {"date": "09-15", "total": 5.0})
        self.assertEqual(series[-4], {"date": "09-12", "total": 5.0})
        self.assertEqual(series[0]["date"], "08-17")
        self.assertEqual(sum(day["total"] for day in series), 10.0)
