"""The operator's side of plans: the deploy-time bootstrap, the system check
that stops a broken ladder, the per-page context and the cancel door.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_more_operations
"""

from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.management import CommandError, call_command
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from . import plans, services
from .checks import check_subscription_plans
from .gate import subscription_context
from .models import CommunityPlanOffer, Subscription, SubscriptionState
from .tests_plans import GOOD, write
from .tests_more_services import LADDER
from .tests import setUpModule as _install_fixture_ladder
from .tests import tearDownModule as _restore_shipped_ladder

User = get_user_model()


def setUpModule():
    _install_fixture_ladder()
    from django.conf import settings

    settings.SUBSCRIPTION_PLANS_FILE = str(write(LADDER))
    plans.reload()


def tearDownModule():
    _restore_shipped_ladder()


class BootstrapTests(TestCase):
    def setUp(self):
        plans.reload()

    def test_a_broken_ladder_stops_the_bootstrap_before_anything_is_made(self):
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write("version: 2\nplans: []\n"))):
            plans.reload()
            with self.assertRaises(CommandError) as caught:
                call_command("bootstrap_plans", stdout=StringIO())
        plans.reload()
        self.assertIn("plans.yaml is not usable", str(caught.exception))
        self.assertFalse(Community.objects.filter(slug="operators").exists())

    def test_with_no_superuser_the_community_is_made_headless_and_offered_everything(self):
        call_command("bootstrap_plans", stdout=StringIO())
        operators = Community.objects.get(slug="operators")
        self.assertIsNone(operators.head)
        self.assertEqual(set(CommunityPlanOffer.objects.filter(community=operators)
                             .values_list("plan_key", flat=True)), set(plans.keys()))

    def test_a_headless_operators_community_gets_the_first_superuser_as_head(self):
        Community.objects.create(name="Operators", slug="operators")
        first = User.objects.create_superuser("first", "f@example.com", "pw")
        User.objects.create_superuser("second", "s@example.com", "pw")
        call_command("bootstrap_plans", stdout=StringIO())
        self.assertEqual(Community.objects.get(slug="operators").head.user, first)

    def test_an_existing_head_is_kept(self):
        chair = Person.objects.create(display_name="Chair")
        Community.objects.create(name="Operators", slug="operators", head=chair)
        User.objects.create_superuser("root", "r@example.com", "pw")
        call_command("bootstrap_plans", stdout=StringIO())
        self.assertEqual(Community.objects.get(slug="operators").head, chair)

    def test_an_inactive_superuser_is_left_alone(self):
        User.objects.create_superuser("retired", "x@example.com", "pw", is_active=False)
        call_command("bootstrap_plans", stdout=StringIO())
        self.assertFalse(Subscription.objects.exists())
        self.assertFalse(Community.objects.get(slug="operators").members.exists())

    def test_a_lapsed_superuser_is_put_back_on_the_admin_plan(self):
        root = User.objects.create_superuser("root", "r@example.com", "pw")
        row = services.subscribe(root, plans.plan("superuser"))
        Subscription.objects.filter(pk=row.pk).update(state=SubscriptionState.LAPSED)
        call_command("bootstrap_plans", stdout=StringIO())
        row.refresh_from_db()
        self.assertEqual((row.state, row.plan_key), (SubscriptionState.ACTIVE, "superuser"))

    def test_a_ladder_without_an_admin_plan_makes_members_and_says_so(self):
        User.objects.create_superuser("root", "r@example.com", "pw")
        out = StringIO()
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write(GOOD))):
            plans.reload()
            call_command("bootstrap_plans", stdout=out)
        plans.reload()
        self.assertIn("no plan for admins", out.getvalue())
        self.assertFalse(Subscription.objects.exists())
        self.assertEqual(list(Community.objects.get(slug="operators").members
                              .values_list("user__username", flat=True)), ["root"])


class RetiredPlanBootstrapTests(TestCase):
    """A plan the ladder retired (`retired:`, 2026-10-01 — zenobia's
    Developer plan to Standard): bootstrap_plans moves its subscriptions and
    community offers to the successor, once, without a charge."""

    def setUp(self):
        plans.reload()

    def test_its_people_and_offers_land_on_the_successor(self):
        member = User.objects.create_user("dev", "d@example.com", "pw")
        row = services.subscribe(member, plans.plan("standard"), force=True)
        Subscription.objects.filter(pk=row.pk).update(plan_key="developer")
        team = Community.objects.create(name="Team", slug="team")
        CommunityPlanOffer.objects.create(community=team, plan_key="developer")
        both = Community.objects.create(name="Both", slug="both")
        CommunityPlanOffer.objects.create(community=both, plan_key="developer")
        CommunityPlanOffer.objects.create(community=both, plan_key="standard")
        out = StringIO()
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(
                write(LADDER + "retired:\n  developer: standard\n"))):
            plans.reload()
            call_command("bootstrap_plans", stdout=out)
            call_command("bootstrap_plans", stdout=StringIO())
        plans.reload()
        row.refresh_from_db()
        self.assertEqual((row.plan_key, row.state), ("standard", SubscriptionState.ACTIVE))
        self.assertFalse(CommunityPlanOffer.objects.filter(plan_key="developer").exists())
        for community in (team, both):
            with self.subTest(community=community.slug):
                self.assertEqual(CommunityPlanOffer.objects.filter(
                    community=community, plan_key="standard").count(), 1)
        self.assertIn("Retired plan 'developer': 1 subscription(s) and 2 community "
                      "offer(s) moved to 'standard'", out.getvalue())


class SystemCheckTests(TestCase):
    def run_check(self, text):
        with override_settings(SUBSCRIPTION_PLANS_FILE=str(write(text))):
            plans.reload()
            try:
                return check_subscription_plans(None)
            finally:
                plans.reload()

    def test_a_broken_ladder_is_one_error_per_problem(self):
        found = self.run_check("version: 2\nplans: []\n")
        self.assertEqual({f.id for f in found}, {"subscriptions.E001"})
        self.assertEqual(len(found), 2)

    def test_a_paid_feature_no_plan_sells_is_a_warning_naming_it(self):
        warnings = self.run_check(GOOD)
        named = " ".join(w.msg for w in warnings)
        self.assertIn("'cyprian' is declared paid but no plan grants it", named)
        self.assertNotIn("'editor'", named)
        self.assertTrue(all(w.id.startswith("subscriptions.W") for w in warnings))

    def test_a_plan_selling_everything_to_members_silences_the_warning(self):
        self.assertEqual(self.run_check(GOOD + "  - key: all\n    name: All\n"
                                               "    all_features: true\n"), [])

    def test_a_plan_selling_everything_to_admins_only_does_not(self):
        self.assertTrue(self.run_check(GOOD + "  - key: all\n    name: All\n"
                                              "    all_features: true\n    for_admins: true\n"))


class ContextAndCancelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.user = User.objects.create_user("member", "m@example.com", "pw")

    def setUp(self):
        plans.reload()

    def request_for(self, user):
        request = RequestFactory().get("/")
        request.user = user
        return request

    def test_an_anonymous_page_gets_no_plan(self):
        self.assertEqual(subscription_context(self.request_for(AnonymousUser())),
                         {"my_plan": None, "my_subscription": None, "plan_locked": False})

    def test_a_member_gets_the_plan_in_force_and_their_row(self):
        row = services.subscribe(self.user, plans.plan("standard"), force=True)
        payload = subscription_context(self.request_for(self.user))
        self.assertEqual((payload["my_plan"].key, payload["my_subscription"]), ("standard", row))

    def test_a_broken_lookup_never_breaks_the_page(self):
        with mock.patch("toto.subscriptions.models.plan_for", side_effect=RuntimeError("db")):
            payload = subscription_context(self.request_for(self.user))
        self.assertEqual((payload["my_plan"], payload["my_subscription"]), (None, None))

    def test_cancelling_with_nothing_to_cancel_goes_back_to_the_plans(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("subscriptions:cancel"))
        self.assertRedirects(response, reverse("subscriptions:plans"),
                             fetch_redirect_response=False)
        self.assertFalse(Subscription.objects.exists())

    def test_cancelling_keeps_the_row_and_answers_with_my_page(self):
        services.subscribe(self.user, plans.plan("standard"), force=True)
        self.client.force_login(self.user)
        response = self.client.post(reverse("subscriptions:cancel"))
        self.assertRedirects(response, reverse("subscriptions:mine"),
                             fetch_redirect_response=False)
        self.assertEqual(Subscription.objects.get(user=self.user).state,
                         SubscriptionState.CANCELLED)
        self.assertEqual(self.client.get(reverse("subscriptions:cancel")).status_code, 405)
