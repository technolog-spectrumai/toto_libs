"""Plans are personal; what a person may hold is the Communities' to offer —
checked live (2026-09-26). The plan for admins is the exception: every Django
superuser may take it, no offer needed, and nobody else ever (2026-09-28);
superuser functionality still needs both the account and the plan. Run under
a host's settings, like tests_enforcement.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.subscriptions.tests_eligibility
"""

from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
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
    for_admins: true
    all_features: true
"""

SUBSCRIBE_SUPERUSER = "/plans/subscribe/superuser/"


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
    """The plan for admins: every superuser's, nobody else's (2026-09-28)."""

    def fresh(self, user):
        return User.objects.get(pk=user.pk)

    def test_the_privilege_alone_is_free_until_the_plan_is_taken(self):
        root = member("root", self.toto, is_superuser=True, is_staff=True)
        self.assertEqual(plan_for(root).key, "free")
        self.assertFalse(superuser_plan_active(root))
        # Eligible with no Community offering it: the account is the rule.
        self.assertFalse(CommunityPlanOffer.objects.filter(plan_key="superuser").exists())
        self.assertTrue(services.is_eligible(root, "superuser"))
        row = services.subscribe(root, plans.plan("superuser"))
        self.assertTrue(row.for_admins)
        self.assertTrue(superuser_plan_active(root))
        self.assertTrue(plan_for(root).grants("anything"))

    def test_a_superuser_in_no_community_takes_it_from_the_plans_page(self):
        root = User.objects.create_superuser("root", password="pw")     # no Person, no Community
        self.client.force_login(root)
        page = self.client.get(reverse("subscriptions:plans")).content.decode()
        self.assertIn('data-testid="for-admins"', page)
        self.assertIn("For administrators", page)
        self.assertIn(f'action="{SUBSCRIBE_SUPERUSER}"', page)
        response = self.client.post(SUBSCRIBE_SUPERUSER)
        self.assertRedirects(response, reverse("subscriptions:mine"), fetch_redirect_response=False)
        row = Subscription.objects.get(user=root)
        self.assertEqual((row.plan_key, row.for_admins, row.forced), ("superuser", True, False))
        self.assertEqual(plan_for(root).key, "superuser")
        self.assertTrue(superuser_plan_active(root))
        self.assertEqual(services.ineligibility_reason(row), "")

    def test_staff_and_members_never_see_or_take_it_even_where_it_is_offered(self):
        # An offer reaching them changes nothing: the plan is the account's.
        CommunityPlanOffer.objects.create(community=self.toto, plan_key="superuser")
        staff = member("staff", self.toto, is_staff=True)
        plain = member("plain", self.toto)
        for user in (staff, plain):
            with self.subTest(user=user.username):
                self.assertFalse(services.is_eligible(user, "superuser"))
                self.assertNotIn("superuser", [p.key for p in services.eligible_plans(user)])
                self.client.force_login(user)
                page = self.client.get(reverse("subscriptions:plans")).content.decode()
                self.assertNotIn(SUBSCRIBE_SUPERUSER, page)
                self.assertNotIn('data-testid="for-admins"', page)
                self.assertEqual(self.client.post(SUBSCRIBE_SUPERUSER).status_code, 404)
                self.assertFalse(Subscription.objects.filter(user=user).exists())
        self.assertFalse(services.is_eligible(AnonymousUser(), "superuser"))

    def test_force_does_not_put_an_ordinary_account_on_it(self):
        plain = member("plain", self.toto)
        staff = member("staff", self.toto, is_staff=True)
        for user in (plain, staff):
            with self.subTest(user=user.username):
                with self.assertRaises(services.IneligiblePlan):
                    services.subscribe(user, plans.plan("superuser"), force=True)
                with self.assertRaises(services.IneligiblePlan):
                    services.subscribe(user, plans.plan("superuser"), force=True, approved_by=user)
                self.assertFalse(Subscription.objects.filter(user=user).exists())

    def test_the_model_refuses_a_live_admin_row_for_an_ordinary_account(self):
        plain = member("plain", self.toto)
        anchor = timezone.now().date()
        row = Subscription(user=plain, plan_key="superuser", anchor_date=anchor)
        with self.assertRaises(ValidationError) as caught:
            row.full_clean()
        self.assertIn("plan_key", caught.exception.message_dict)
        # save() refuses too — a script, a fixture or a shell cannot bypass it.
        with self.assertRaises(ValidationError):
            Subscription.objects.create(user=plain, plan_key="superuser", anchor_date=anchor)
        self.assertFalse(Subscription.objects.filter(user=plain).exists())
        # Nor by switching an existing row with a partial save.
        row = services.subscribe(plain, plans.plan("standard"))
        row.plan_key = "superuser"
        with self.assertRaises(ValidationError):
            row.save(update_fields=["plan_key"])
        row.refresh_from_db()
        self.assertEqual((row.plan_key, row.for_admins), ("standard", False))
        # The flag is the plan's, never the caller's.
        row.for_admins = True
        row.save()
        row.refresh_from_db()
        self.assertFalse(row.for_admins)

    def test_demoting_a_superuser_takes_the_plan_away_and_the_sweep_says_why(self):
        root = member("root", self.harbour, is_superuser=True, is_staff=True)
        services.subscribe(root, plans.plan("superuser"))
        self.assertTrue(superuser_plan_active(root))
        root.is_superuser = False
        root.save(update_fields=["is_superuser"])
        root = self.fresh(root)
        self.assertEqual(plan_for(root).key, "free")
        self.assertFalse(superuser_plan_active(root))
        self.assertFalse(services.is_eligible(root, "superuser"))
        row = Subscription.objects.get(user=root)
        self.assertEqual(services.ineligibility_reason(row), "not-superuser")
        # The row is still live, so it cannot be written as live again...
        with self.assertRaises(ValidationError):
            row.save()
        # ...but the sweep can write it down, with the reason.
        counts = services.run_billing()
        self.assertEqual(counts["lapsed"], 1)
        row.refresh_from_db()
        self.assertEqual((row.state, row.lapse_reason, row.for_admins),
                         (SubscriptionState.LAPSED, "not-superuser", True))
        with self.assertRaises(services.IneligiblePlan):
            services.subscribe(root, plans.plan("superuser"))

    def test_a_month_on_it_never_keeps_a_demoted_row_live(self):
        """Arrears keep a row live; a demoted holder's row is lapsed instead."""
        root = member("root", self.harbour, is_superuser=True, is_staff=True)
        row = services.subscribe(root, plans.plan("superuser"))
        User.objects.filter(pk=root.pk).update(is_superuser=False)
        row = Subscription.objects.select_related("user").get(pk=row.pk)
        services._enter_arrears(row)
        row.refresh_from_db()
        self.assertEqual((row.state, row.lapse_reason), (SubscriptionState.LAPSED, "not-superuser"))
        row.arrears_since = timezone.now()
        row.save(update_fields=["arrears_since"])        # lapsed: not live, so allowed
        services._clear_arrears(row)
        row.refresh_from_db()
        self.assertEqual(row.state, SubscriptionState.LAPSED)

    def test_the_flag_follows_the_plan_down_to_developer_and_back(self):
        root = User.objects.create_superuser("root", password="pw")     # in no Community
        self.client.force_login(root)
        steps = (("superuser", True, "superuser"), ("developer", False, "developer"),
                 ("superuser", True, "superuser"))
        for key, flag, in_force in steps:
            with self.subTest(step=key):
                self.assertEqual(self.client.post(f"/plans/subscribe/{key}/").status_code, 302)
                row = Subscription.objects.get(user=root)
                self.assertEqual((row.plan_key, row.for_admins), (key, flag))
                self.assertEqual(plan_for(self.fresh(root)).key, in_force)
        self.assertEqual(Subscription.objects.filter(user=root).count(), 1)

    def test_the_admin_shows_the_flag_read_only_and_filters_by_it(self):
        root = User.objects.create_superuser("root", password="pw")
        row = services.subscribe(root, plans.plan("superuser"))
        self.client.force_login(root)
        listing = self.client.get("/admin/subscriptions/subscription/")
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, "column-for_admins")
        self.assertContains(listing, "for_admins__exact=1")
        change = self.client.get(f"/admin/subscriptions/subscription/{row.pk}/change/")
        self.assertEqual(change.status_code, 200)
        self.assertContains(change, "field-for_admins")
        self.assertNotContains(change, 'name="for_admins"')

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
        self.assertNotIn("for_admins", form.fields)          # the plan decides, never a form
        staff = member("staff", self.toto, is_staff=True)
        form = SubscriptionAdminForm({"user": staff.pk, "plan_key": "superuser", "state": "active",
                                      "anchor_date": anchor})
        self.assertFalse(form.is_valid())
        form = SubscriptionAdminForm({"user": plain.pk, "plan_key": "developer", "state": "active",
                                      "anchor_date": anchor})
        self.assertFalse(form.is_valid())
        self.assertIn("No Community", str(form.errors))
        form = SubscriptionAdminForm({"user": plain.pk, "plan_key": "free", "state": "active",
                                      "anchor_date": anchor})
        self.assertTrue(form.is_valid(), form.errors)
        # A superuser needs no offer (2026-09-28), and the saved row says so.
        form = SubscriptionAdminForm({"user": root.pk, "plan_key": "superuser", "state": "active",
                                      "anchor_date": anchor})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.save().for_admins)

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
        services.subscribe(root, plans.plan("superuser"))
        self.assertEqual(view(request), "ok")
        self.assertIsNotNone(_resolve_dashboard_item(tile, root))
