"""Plans are personal; what a person may hold is the Communities' to offer —
checked live, with the superuser plan needing both the account and the grant
(2026-09-26). Run under a host's settings, like tests_enforcement.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_eligibility
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from . import plans, services
from .models import CommunityPlanOffer, Subscription, SubscriptionState, plan_for, superuser_plan_active
from .tests import setUpModule as _install_fixture_ladder
from .tests import tearDownModule as _restore_shipped_ladder

User = get_user_model()

ADMIN_LADDER = """version: 1
plans:
  - key: free
    name: Free
    default: true
  - key: standard
    name: Standard
    units: 10
    features: [editor]
  - key: developer
    name: Developer
    units: 20
    features: [editor, gitea]
  - key: superuser
    name: Superuser
    admin_only: true
    all_features: true
"""


def setUpModule():
    _install_fixture_ladder()
    # This module's own ladder, over the fixture one: an admin-only plan and
    # two buyable tiers.
    import tempfile
    from pathlib import Path

    from django.conf import settings

    global _PATH
    _PATH = Path(tempfile.mkdtemp(prefix="toto-eligibility-")) / "plans.yaml"
    _PATH.write_text(ADMIN_LADDER)
    settings.SUBSCRIPTION_PLANS_FILE = str(_PATH)
    plans.reload()


def tearDownModule():
    _restore_shipped_ladder()


def member(name, *communities, **flags):
    user = User.objects.create_user(name, f"{name}@example.com", "pw", **flags)
    person = Person.objects.create(user=user, display_name=name)
    for community in communities:
        person.communities.add(community)
    return user


class EligibilityBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.toto = Community.objects.create(name="toto", slug="toto", org_type="company")
        cls.dev = Community.objects.create(name="toto-dev", slug="toto-dev", parent=cls.toto)
        cls.harbour = Community.objects.create(name="Quiet Harbour", slug="quiet-harbour")
        CommunityPlanOffer.objects.create(community=cls.toto, plan_key="standard")
        CommunityPlanOffer.objects.create(community=cls.dev, plan_key="developer")

    def setUp(self):
        plans.reload()


class CommunityScopeTests(EligibilityBase):
    def test_offers_inherit_down_the_tree_and_never_across(self):
        dev = member("dev", self.dev)
        parent_only = member("parent", self.toto)
        lonely = member("lonely", self.harbour)
        self.assertTrue(services.is_eligible(dev, "standard"))          # from toto, the parent
        self.assertTrue(services.is_eligible(dev, "developer"))         # toto-dev's own
        self.assertTrue(services.is_eligible(parent_only, "standard"))
        self.assertFalse(services.is_eligible(parent_only, "developer"))  # a child's offer stays the child's
        self.assertFalse(services.is_eligible(lonely, "standard"))
        self.assertFalse(services.is_eligible(lonely, "developer"))
        self.assertTrue(services.is_eligible(lonely, "free"))

    def test_the_isolated_community_cannot_buy_and_the_service_refuses_too(self):
        lonely = member("lonely", self.harbour)
        self.client.force_login(lonely)
        response = self.client.post("/plans/subscribe/developer/")
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(lonely, plans.plan("developer"))
        self.assertFalse(Subscription.objects.filter(user=lonely).exists())

    def test_leaving_the_community_takes_the_plan_away_on_the_next_request(self):
        dev = member("dev", self.dev)
        services.subscribe(dev, plans.plan("developer"))
        self.assertEqual(plan_for(dev).key, "developer")
        dev.community_profile.communities.remove(self.dev)
        self.assertEqual(plan_for(dev).key, "free")
        self.assertEqual(Subscription.objects.get(user=dev).state, SubscriptionState.ACTIVE)
        counts = services.run_billing()
        self.assertEqual(counts["lapsed"], 1)
        row = Subscription.objects.get(user=dev)
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.LAPSED, "withdrawn"))

    def test_withdrawing_the_offer_does_the_same(self):
        dev = member("dev", self.dev)
        services.subscribe(dev, plans.plan("developer"))
        CommunityPlanOffer.objects.filter(community=self.dev, plan_key="developer").delete()
        self.assertEqual(plan_for(dev).key, "free")
        services.run_billing()
        self.assertEqual(Subscription.objects.get(user=dev).lapse_reason, "withdrawn")

    def test_an_expired_subscription_grants_nothing_and_is_lapsed(self):
        dev = member("dev", self.dev)
        row = services.subscribe(dev, plans.plan("developer"))
        row.expires_at = timezone.now() + timedelta(days=1)
        row.save(update_fields=["expires_at"])
        self.assertEqual(plan_for(dev).key, "developer")
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        self.assertEqual(plan_for(dev).key, "free")
        services.run_billing()
        row.refresh_from_db()
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.LAPSED, "expired"))
        # Subscribing again clears the reason.
        services.subscribe(dev, plans.plan("developer"))
        row.refresh_from_db()
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.ACTIVE, ""))

    def test_staff_may_hold_any_buyable_plan_without_an_offer(self):
        staff = member("staff", self.harbour, is_staff=True)
        services.subscribe(staff, plans.plan("developer"))
        self.assertEqual(plan_for(staff).key, "developer")

    def test_an_operators_grant_needs_no_offer_but_still_expires(self):
        lonely = member("lonely", self.harbour)
        row = services.subscribe(lonely, plans.plan("developer"), force=True)
        self.assertTrue(row.forced)
        self.assertEqual(plan_for(lonely).key, "developer")
        self.assertEqual(services.ineligibility_reason(row), "")
        row.expires_at = timezone.now() - timedelta(seconds=1)
        row.save(update_fields=["expires_at"])
        self.assertEqual(plan_for(lonely).key, "free")
        # Buying it again through an offer clears the operator's mark.
        CommunityPlanOffer.objects.create(community=self.harbour, plan_key="developer")
        self.assertFalse(services.subscribe(lonely, plans.plan("developer")).forced)


class SuperuserPlanTests(EligibilityBase):
    def test_the_privilege_alone_is_free_and_the_plan_alone_is_nothing(self):
        root = member("root", self.toto, is_superuser=True, is_staff=True)
        self.assertEqual(plan_for(root).key, "free")
        self.assertFalse(superuser_plan_active(root))
        self.assertFalse(services.is_eligible(root, "superuser"))     # nobody offers it yet
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(root, plans.plan("superuser"))
        # An ordinary account with the row anyway: the plan grants nothing.
        plain = member("plain", self.toto)
        Subscription.objects.create(user=plain, plan_key="superuser",
                                    anchor_date=timezone.now().date())
        self.assertEqual(plan_for(plain).key, "free")
        self.assertFalse(superuser_plan_active(plain))
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(plain, plans.plan("superuser"), force=True)
        services.run_billing()
        self.assertEqual(Subscription.objects.get(user=plain).lapse_reason, "not-superuser")

    def test_a_community_offer_plus_the_account_is_both(self):
        root = member("root", self.toto, is_superuser=True, is_staff=True)
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="superuser")
        self.assertTrue(services.is_eligible(root, "superuser"))
        services.subscribe(root, plans.plan("superuser"))
        self.assertTrue(superuser_plan_active(root))
        self.assertTrue(plan_for(root).grants("anything"))
        # Staff in the same Community: offered, still refused.
        staff = member("staff", self.toto, is_staff=True)
        self.assertFalse(services.is_eligible(staff, "superuser"))
        self.client.force_login(staff)
        self.assertEqual(self.client.post("/plans/subscribe/superuser/").status_code, 404)

    def test_bootstrap_puts_every_superuser_on_the_plan_once(self):
        root = User.objects.create_superuser("root", password="pw")     # no Person yet
        second = member("second", self.harbour, is_superuser=True, is_staff=True)
        out = StringIO()
        call_command("bootstrap_plans", stdout=out)
        call_command("bootstrap_plans", stdout=out)
        operators = Community.objects.get(slug="operators")
        self.assertEqual(operators.head.user, root)
        self.assertEqual(set(operators.members.values_list("user__username", flat=True)),
                         {"root", "second"})
        self.assertEqual(CommunityPlanOffer.objects.filter(community=operators).count(),
                         len(plans.all_plans()))
        self.assertEqual(Subscription.objects.filter(plan_key="superuser").count(), 2)
        for user in (root, second):
            self.assertTrue(superuser_plan_active(User.objects.get(pk=user.pk)))

    def test_bootstrap_keeps_a_plan_a_superuser_chose(self):
        root = member("root", self.dev, is_superuser=True, is_staff=True)
        services.subscribe(root, plans.plan("developer"))
        call_command("bootstrap_plans", stdout=StringIO())
        self.assertEqual(plan_for(root).key, "developer")

    def test_only_superusers_reach_the_mapping_tabs(self):
        staff = member("staff", self.toto, is_staff=True)
        root = member("root", self.toto, is_superuser=True, is_staff=True)
        for user, status in ((staff, 403), (root, 200)):
            self.client.force_login(user)
            for name in ("subscriptions:audience", "subscriptions:discounts"):
                from django.urls import reverse

                with self.subTest(user=user.username, tab=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, status)
        self.client.force_login(staff)
        page = self.client.get("/plans/").content.decode()
        self.assertNotIn("Discounts", page.split("<main")[-1][:4000])

    def test_the_admin_form_refuses_what_the_service_refuses(self):
        from .admin import SubscriptionAdminForm

        plain = member("plain", self.harbour)
        root = member("root", self.toto, is_superuser=True, is_staff=True)
        anchor = timezone.now().date()
        form = SubscriptionAdminForm({"user": plain.pk, "plan_key": "superuser", "state": "active",
                                      "anchor_date": anchor})
        self.assertFalse(form.is_valid())
        self.assertIn("superusers only", str(form.errors))
        form = SubscriptionAdminForm({"user": plain.pk, "plan_key": "developer", "state": "active",
                                      "anchor_date": anchor})
        self.assertFalse(form.is_valid())
        self.assertIn("No Community", str(form.errors))
        form = SubscriptionAdminForm({"user": plain.pk, "plan_key": "free", "state": "active",
                                      "anchor_date": anchor})
        self.assertTrue(form.is_valid(), form.errors)
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="superuser")
        form = SubscriptionAdminForm({"user": root.pk, "plan_key": "superuser", "state": "active",
                                      "anchor_date": anchor})
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_decorator_and_the_dashboard_arm_ask_for_both(self):
        from django.core.exceptions import PermissionDenied
        from django.test import RequestFactory

        from toto.core.views import _resolve_dashboard_item

        from .gate import superuser_plan_required

        @superuser_plan_required
        def view(request):
            return "ok"

        root = member("root", self.toto, is_superuser=True, is_staff=True)
        request = RequestFactory().get("/")
        request.user = root
        tile = {"title": "Ops", "description": "", "icon": "x", "link": "", "visibility": "superuser"}
        with self.assertRaises(PermissionDenied):
            view(request)
        self.assertIsNone(_resolve_dashboard_item(tile, root))
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="superuser")
        services.subscribe(root, plans.plan("superuser"))
        self.assertEqual(view(request), "ok")
        self.assertIsNotNone(_resolve_dashboard_item(tile, root))
