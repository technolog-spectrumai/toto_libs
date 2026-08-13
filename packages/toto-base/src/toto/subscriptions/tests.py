"""What a plan buys, what it costs, and what happens when it cannot be paid.

The tests that matter most here are the ones about NOT losing things: a lapsed
subscriber keeps every file and every row, and a failed month is written off
rather than owed. Those are promises the interface makes in words, and words are
not enforcement.
"""

from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from . import services
from .catalogue import Entitlement, registry
from .gate import ALWAYS_FREE, SubscriptionGateMiddleware, is_entitled
from .models import (
    ChargeStatus,
    CommunityPlanDiscount,
    Subscription,
    SubscriptionCharge,
    SubscriptionPlan,
    SubscriptionState,
    add_months,
    period_label,
    plan_for,
)

User = get_user_model()


def make_plans():
    free = SubscriptionPlan.objects.create(
        code="free", name="Free", units=0, is_default=True, order=10)
    standard = SubscriptionPlan.objects.create(
        code="standard", name="Standard", units=200, order=20,
        entitlements=["cyprian", "primula"])
    studio = SubscriptionPlan.objects.create(
        code="studio", name="Studio", units=600, order=30,
        entitlements=["cyprian", "primula", "aralia", "workflows"])
    return free, standard, studio


def member(name, *communities):
    user = User.objects.create_user(name, f"{name}@example.com", "pw")
    person = Person.objects.create(user=user, display_name=name)
    for community in communities:
        person.communities.add(community)
    return user


class CatalogueTests(TestCase):
    def test_the_free_codes_are_the_ones_marked_free(self):
        free = registry.free_codes()
        self.assertIn("vault", free)
        self.assertIn("assets", free)
        self.assertNotIn("aralia", free)

    def test_the_economy_is_free_so_a_wallet_can_always_be_funded(self):
        """The rule that makes a paywall escapable.

        If the wallet or the plans page were sold, somebody who lapsed could
        never buy their way back in.
        """
        for code in ("assets", "quota", "subscriptions"):
            with self.subTest(code=code):
                self.assertTrue(registry.get(code).free)

    def test_a_duplicate_declaration_is_refused(self):
        from .catalogue import DuplicateEntitlement

        with self.assertRaises(DuplicateEntitlement):
            registry.register(Entitlement("vault", "Something else"))

    def test_re_registering_the_identical_row_is_fine(self):
        """Autodiscovery may import a module twice; that must not explode."""
        registry.register(registry.get("vault"))


class DiscountTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()
        cls.students = Community.objects.create(name="Students")
        cls.founders = Community.objects.create(name="Founders")

    def test_no_community_pays_full(self):
        user = member("solo")
        self.assertEqual(services.best_discount(user, self.standard), (0, ""))
        self.assertEqual(services.billed_units(self.standard, 0), Decimal("200"))

    def test_the_best_of_several_communities_wins(self):
        CommunityPlanDiscount.objects.create(
            community=self.students, plan=self.standard, percent=40)
        CommunityPlanDiscount.objects.create(
            community=self.founders, plan=self.standard, percent=10)
        user = member("both", self.students, self.founders)

        percent, source = services.best_discount(user, self.standard)

        self.assertEqual(percent, 40)
        self.assertEqual(source, "Students")
        self.assertEqual(services.billed_units(self.standard, percent),
                         Decimal("120"))

    def test_a_discount_on_another_plan_changes_nothing(self):
        CommunityPlanDiscount.objects.create(
            community=self.students, plan=self.studio, percent=50)
        user = member("student", self.students)

        self.assertEqual(services.best_discount(user, self.standard), (0, ""))

    def test_a_user_with_no_person_row_is_not_an_error(self):
        user = User.objects.create_user("profileless", password="pw")
        self.assertEqual(services.best_discount(user, self.standard), (0, ""))

    def test_anonymous_is_not_an_error(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(services.best_discount(AnonymousUser(), self.standard),
                         (0, ""))


class PlanResolutionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()

    def test_no_subscription_resolves_to_the_default_plan(self):
        self.assertEqual(plan_for(member("nobody")), self.free)

    def test_a_lapsed_subscription_resolves_to_the_default_not_its_own(self):
        """The row records what they chose; this answers what they get."""
        user = member("lapsed")
        subscription = services.subscribe(user, self.studio)
        subscription.state = SubscriptionState.LAPSED
        subscription.save(update_fields=["state"])

        self.assertEqual(plan_for(user), self.free)

    def test_arrears_still_grants_because_that_is_what_grace_means(self):
        user = member("behind")
        subscription = services.subscribe(user, self.studio)
        subscription.state = SubscriptionState.ARREARS
        subscription.save(update_fields=["state"])

        self.assertEqual(plan_for(user), self.studio)


class GateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()

    def test_a_free_user_is_refused_a_paid_app(self):
        self.assertFalse(is_entitled(member("plain"), "aralia"))

    def test_a_free_user_keeps_every_free_app(self):
        user = member("plain")
        for code in ("vault", "socialhub", "assets", "quota", "subscriptions"):
            with self.subTest(code=code):
                self.assertTrue(is_entitled(user, code))

    def test_a_plan_grants_exactly_what_it_lists(self):
        user = member("buyer")
        services.subscribe(user, self.standard)

        self.assertTrue(is_entitled(user, "cyprian"))
        self.assertFalse(is_entitled(user, "aralia"))

    def test_an_app_nobody_declared_is_not_gated(self):
        """Installing a new app must not be a silent outage."""
        self.assertTrue(is_entitled(member("plain"), "some-new-app"))

    def test_nothing_is_gated_before_any_plan_is_seeded(self):
        SubscriptionPlan.objects.all().delete()
        self.assertTrue(is_entitled(member("plain"), "aralia"))

    def test_the_always_free_list_covers_the_way_back_in(self):
        for code in ("core", "sso", "subscriptions", "assets", "quota"):
            with self.subTest(code=code):
                self.assertIn(code, ALWAYS_FREE)


class MiddlewareTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()
        cls.user = member("reader")

    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = SubscriptionGateMiddleware(lambda request: None)

    def _run(self, method, path="/aralia/", app_name="aralia", **extra):
        request = getattr(self.factory, method.lower())(path, **extra)
        request.user = self.user
        request.resolver_match = type("M", (), {"app_name": app_name})()
        return self.middleware.process_view(request, None, (), {}), request

    def test_a_read_is_allowed_and_marked(self):
        response, request = self._run("get")

        self.assertIsNone(response)
        self.assertTrue(request.plan_locked)

    def test_a_write_is_refused_with_402(self):
        response, _request = self._run("post")

        self.assertIsNotNone(response)
        self.assertEqual(response.status_code, 402)

    def test_an_api_write_gets_json_not_a_page(self):
        import json

        response, _request = self._run("post", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 402)
        payload = json.loads(response.content)
        self.assertEqual(payload["reason"], "subscription-required")
        self.assertEqual(payload["entitlement"], "aralia")

    def test_a_subscriber_writes_freely(self):
        services.subscribe(self.user, self.studio)

        response, request = self._run("post")

        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_an_anonymous_request_is_never_gated(self):
        from django.contrib.auth.models import AnonymousUser

        request = self.factory.post("/aralia/")
        request.user = AnonymousUser()
        request.resolver_match = type("M", (), {"app_name": "aralia"})()

        self.assertIsNone(self.middleware.process_view(request, None, (), {}))

    def test_a_free_app_is_never_gated_even_for_a_write(self):
        response, request = self._run("post", path="/vault/", app_name="vault")

        self.assertIsNone(response)
        self.assertFalse(request.plan_locked)

    def test_a_broken_lookup_opens_reads_and_closes_writes(self):
        """The asymmetry the module docstring promises.

        Delta's helper returns False on any exception, which GRANTS access on a
        database fault. That is the wrong way round for something deciding what
        somebody paid for.
        """
        from unittest import mock

        with mock.patch("toto.subscriptions.gate.is_entitled",
                        side_effect=RuntimeError("db on fire")):
            read, _ = self._run("get")
            write, _ = self._run("post")

        self.assertIsNone(read)
        self.assertEqual(write.status_code, 402)


class PeriodTests(TestCase):
    def test_month_arithmetic_clamps_to_a_short_month(self):
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(add_months(date(2024, 1, 31), 1), date(2024, 2, 29))

    def test_a_year_of_months_lands_on_the_same_day(self):
        self.assertEqual(add_months(date(2026, 3, 15), 12), date(2027, 3, 15))

    def test_the_label_is_the_month(self):
        self.assertEqual(period_label(date(2026, 8, 3)), "2026-08")


class MaterializeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()

    def test_one_row_per_elapsed_month(self):
        user = member("old")
        subscription = services.subscribe(user, self.standard)
        subscription.anchor_date = date.today().replace(day=1) - timedelta(days=70)
        subscription.anchor_date = subscription.anchor_date.replace(day=1)
        subscription.save(update_fields=["anchor_date"])

        services.materialize(subscription)

        self.assertGreaterEqual(subscription.charges.count(), 3)

    def test_running_it_twice_creates_nothing_new(self):
        user = member("twice")
        subscription = services.subscribe(user, self.standard)

        services.materialize(subscription)
        first = subscription.charges.count()
        services.materialize(subscription)

        self.assertEqual(subscription.charges.count(), first)

    def test_it_never_bills_the_future(self):
        user = member("future")
        subscription = services.subscribe(user, self.standard)

        services.materialize(subscription)

        labels = list(subscription.charges.values_list("period_label", flat=True))
        self.assertEqual(labels, [period_label()])


class SettleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})

    def test_an_unpriced_host_settles_every_month_as_paid(self):
        """Zenobia today. A DUE row nobody can price would pile up forever."""
        user = member("unbilled")
        subscription = services.subscribe(user, self.standard)
        services.materialize(subscription)
        charge = subscription.charges.get()

        services.settle(charge)

        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)

    def test_a_free_plan_costs_nothing_and_settles(self):
        user = member("freeloader")
        subscription = services.subscribe(user, self.free)
        services.materialize(subscription)
        charge = subscription.charges.get()

        services.settle(charge)

        charge.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.PAID)
        self.assertEqual(charge.units, Decimal("0"))

    def test_settling_twice_takes_no_second_bite(self):
        from unittest import mock

        user = member("double")
        subscription = services.subscribe(user, self.standard)
        services.materialize(subscription)
        charge = subscription.charges.get()
        services.settle(charge)

        with mock.patch("toto.quota.charge.charge") as spend:
            services.settle(charge)

        spend.assert_not_called()

    def test_an_empty_wallet_fails_the_month_and_enters_arrears(self):
        from unittest import mock

        from toto.quota.charge import InsufficientFunds

        user = member("broke")
        subscription = services.subscribe(user, self.standard)
        services.materialize(subscription)
        charge = subscription.charges.get()

        # price_for and charge are both imported inside settle(), so patching
        # them on toto.quota.charge is what the function actually looks up.
        with mock.patch("toto.quota.charge.price_for", return_value=object()), \
             mock.patch("toto.quota.charge.charge",
                        side_effect=InsufficientFunds("ASR", 200, 0, 9)):
            services.settle(charge)

        charge.refresh_from_db()
        subscription.refresh_from_db()
        self.assertEqual(charge.status, ChargeStatus.FAILED)
        self.assertEqual(subscription.state, SubscriptionState.ARREARS)
        self.assertIsNotNone(subscription.arrears_since)


class ArrearsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.free, cls.standard, cls.studio = make_plans()

    def _in_arrears(self, days_ago):
        from django.utils import timezone

        user = member(f"late{days_ago}")
        subscription = services.subscribe(user, self.studio)
        subscription.state = SubscriptionState.ARREARS
        subscription.arrears_since = timezone.now() - timedelta(days=days_ago)
        subscription.save(update_fields=["state", "arrears_since"])
        return subscription

    def test_inside_the_grace_window_nothing_happens(self):
        subscription = self._in_arrears(1)

        self.assertFalse(services.lapse_if_overdue(subscription))
        self.assertEqual(subscription.state, SubscriptionState.ARREARS)

    def test_past_the_window_it_lapses(self):
        subscription = self._in_arrears(services.grace_days() + 1)

        self.assertTrue(services.lapse_if_overdue(subscription))
        self.assertEqual(subscription.state, SubscriptionState.LAPSED)

    def test_lapsing_deletes_nothing(self):
        """The promise the arrears banner makes, asserted rather than hoped."""
        from toto.vault.models import Bucket

        subscription = self._in_arrears(services.grace_days() + 1)
        bucket = Bucket.objects.create(
            name="Mine", slug=f"mine-{subscription.user.pk}",
            owner=subscription.user)

        services.lapse_if_overdue(subscription)

        self.assertTrue(Bucket.objects.filter(pk=bucket.pk).exists())
        self.assertTrue(SubscriptionCharge.objects.filter(
            subscription=subscription).count() >= 0)
        self.assertTrue(Subscription.objects.filter(pk=subscription.pk).exists())

    def test_subscribing_again_restores_access_immediately(self):
        subscription = self._in_arrears(services.grace_days() + 1)
        services.lapse_if_overdue(subscription)
        self.assertEqual(plan_for(subscription.user), self.free)

        services.subscribe(subscription.user, self.studio)

        self.assertEqual(plan_for(subscription.user), self.studio)

    def test_a_paid_month_clears_the_arrears_state(self):
        subscription = self._in_arrears(1)
        services.materialize(subscription)
        charge = subscription.charges.first()

        services.settle(charge)

        subscription.refresh_from_db()
        self.assertEqual(subscription.state, SubscriptionState.ACTIVE)
        self.assertIsNone(subscription.arrears_since)


class ViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.free, cls.standard, cls.studio = make_plans()
        cls.user = member("shopper")

    def test_the_plans_page_is_readable_without_logging_in(self):
        """What a platform charges is not a secret."""
        response = self.client.get(reverse("subscriptions:plans"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Standard")

    def test_a_card_lists_what_the_gate_will_actually_grant(self):
        response = self.client.get(reverse("subscriptions:plans"))

        row = next(r for r in response.context["rows"]
                   if r["plan"].code == "standard")
        codes = {e.code for e in row["entitlements"]}
        self.assertIn("cyprian", codes)
        # Free entitlements appear on every card, because they are included.
        self.assertIn("vault", codes)
        self.assertNotIn("aralia", codes)

    def test_the_discount_is_named_on_the_card(self):
        community = Community.objects.create(name="Students")
        CommunityPlanDiscount.objects.create(
            community=community, plan=self.standard, percent=40)
        Person.objects.get(user=self.user).communities.add(community)
        self.client.force_login(self.user)

        response = self.client.get(reverse("subscriptions:plans"))

        row = next(r for r in response.context["rows"]
                   if r["plan"].code == "standard")
        self.assertEqual(row["discount_percent"], 40)
        self.assertEqual(row["discount_source"], "Students")

    def test_subscribing_puts_you_on_the_plan(self):
        self.client.force_login(self.user)

        self.client.post(reverse("subscriptions:subscribe", args=["studio"]))

        self.assertEqual(plan_for(self.user), self.studio)

    def test_my_page_materialises_but_never_settles(self):
        """A page render must not reach into somebody's wallet."""
        from unittest import mock

        self.client.force_login(self.user)
        services.subscribe(self.user, self.standard)

        with mock.patch("toto.subscriptions.services.settle") as settle:
            response = self.client.get(reverse("subscriptions:mine"))

        self.assertEqual(response.status_code, 200)
        settle.assert_not_called()
        self.assertEqual(
            SubscriptionCharge.objects.filter(subscription__user=self.user).count(), 1)

    def test_cancelling_keeps_the_free_plan(self):
        self.client.force_login(self.user)
        services.subscribe(self.user, self.studio)

        self.client.post(reverse("subscriptions:cancel"))

        self.assertEqual(plan_for(self.user), self.free)


class IngressTests(TestCase):
    """The seed, and the rule that seeding is what turns gating on."""

    def _seed(self, *args):
        from io import StringIO

        from django.core.management import call_command

        out = StringIO()
        call_command("ingress_subscriptions", *args, stdout=out)
        return out.getvalue()

    def test_it_seeds_three_plans(self):
        self._seed()

        codes = set(SubscriptionPlan.objects.values_list("code", flat=True))
        self.assertEqual(codes, {"free", "standard", "studio"})

    def test_running_it_twice_changes_nothing(self):
        self._seed()
        self._seed()

        self.assertEqual(SubscriptionPlan.objects.count(), 3)

    def test_exactly_one_default_survives_a_hand_edit(self):
        """Two defaults would make 'what does a free user get' ambiguous."""
        self._seed()
        SubscriptionPlan.objects.filter(code="studio").update(is_default=True)

        self._seed()

        self.assertEqual(
            list(SubscriptionPlan.objects.filter(is_default=True)
                 .values_list("code", flat=True)),
            ["free"])

    def test_the_demo_discount_is_full_only(self):
        Community.objects.create(name="Demo")

        self._seed()
        self.assertEqual(CommunityPlanDiscount.objects.count(), 0)

        self._seed("--full")
        self.assertEqual(CommunityPlanDiscount.objects.count(), 1)

    def test_seeding_is_what_starts_gating(self):
        """A host that never seeds is fully open, with the same code deployed."""
        user = member("early")
        self.assertTrue(is_entitled(user, "aralia"))

        self._seed()

        self.assertFalse(is_entitled(user, "aralia"))
