"""Automatic assignment to the best eligible plan (``autoplan``, 2026-10-11):
on a host that turns it on, each person holds the best plan their communities
offer, follows every membership change, and chooses nothing.

    manage.py test toto.subscriptions.tests_autoplan
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

from . import autoplan, plans, services
from .models import CommunityPlanOffer, Subscription, plan_for

User = get_user_model()

LADDER = """
version: 1
plans:
  - key: free
    name: Free
    default: true
    units: 0
    order: 10
    features: []
  - key: team
    name: Team
    units: 0
    order: 20
    features: [editor]
  - key: priced
    name: Priced
    units: 50
    order: 30
    features: [editor, repo]
  - key: superuser
    name: Superuser
    for_admins: true
    all_features: true
"""


def setUpModule():
    global _PATH, _OLD
    from django.conf import settings

    _OLD = getattr(settings, "SUBSCRIPTION_PLANS_FILE", "")
    _PATH = Path(tempfile.mkdtemp(prefix="toto-autoplan-")) / "plans.yaml"
    _PATH.write_text(LADDER)
    settings.SUBSCRIPTION_PLANS_FILE = str(_PATH)
    plans.reload()


def tearDownModule():
    from django.conf import settings

    settings.SUBSCRIPTION_PLANS_FILE = _OLD
    plans.reload()


@override_settings(SUBSCRIPTION_AUTO_ASSIGN=True, SUBSCRIPTION_ENFORCEMENT=False)
class AutoAssignTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.everyone = Community.objects.create(name="Everyone", slug="everyone")
        cls.moderators = Community.objects.create(name="Moderators", slug="moderators")
        CommunityPlanOffer.objects.create(community=cls.everyone, plan_key="free")
        CommunityPlanOffer.objects.create(community=cls.moderators, plan_key="team")
        CommunityPlanOffer.objects.create(community=cls.moderators, plan_key="priced")
        cls.mia = User.objects.create_user("mia", password="pw")
        cls.person = Person.objects.create(user=cls.mia, display_name="Mia")
        cls.person.communities.add(cls.everyone)

    def setUp(self):
        autoplan.connect()

    def test_joining_and_leaving_a_community_moves_the_plan(self):
        self.assertEqual(plan_for(self.mia).key, "free")
        self.person.communities.add(self.moderators)
        self.assertEqual(plan_for(self.mia).key, "team")
        self.assertEqual(Subscription.objects.get(user=self.mia).plan_key, "team")
        self.person.communities.remove(self.moderators)
        self.assertEqual(plan_for(self.mia).key, "free")
        self.assertEqual(Subscription.objects.get(user=self.mia).plan_key, "free")

    def test_the_community_side_moves_its_members_too(self):
        self.moderators.members.add(self.person)
        self.assertEqual(plan_for(self.mia).key, "team")
        self.moderators.members.clear()
        self.assertEqual(plan_for(self.mia).key, "free")

    def test_a_plan_that_costs_units_is_a_purchase_not_a_role(self):
        self.person.communities.add(self.moderators)
        self.assertEqual(autoplan.best_plan_for(self.mia).key, "team")

    def test_staff_hold_what_their_communities_offer_like_anybody(self):
        ops = User.objects.create_user("ops", password="pw", is_staff=True)
        person = Person.objects.create(user=ops, display_name="Ops")
        person.communities.add(self.everyone)
        self.assertEqual(autoplan.best_plan_for(ops).key, "free")
        person.communities.add(self.moderators)
        self.assertEqual(plan_for(ops).key, "team")

    def test_a_forced_row_and_a_superuser_are_left_alone(self):
        services.subscribe(self.mia, plans.plan("team"), force=True)
        self.assertFalse(autoplan.assign_best(self.mia))
        root = User.objects.create_superuser("root", password="pw")
        Person.objects.create(user=root, display_name="Root").communities.add(self.everyone)
        services.subscribe(root, plans.admin_plan())
        self.assertFalse(autoplan.assign_best(root))
        self.assertEqual(plan_for(root).key, "superuser")

    def test_a_superuser_made_at_the_start_lands_on_the_plan_for_admins(self):
        """``bootstrap_plans`` makes a superuser a member of the operators'
        community, which is offered every plan: that membership must not put
        them on the best ordinary plan before the command reaches the plan
        for admins (it did on Dracena's first start with plans, 2026-10-11,
        and the admin lost the superuser pages until 2026-10-12)."""
        import io

        from django.core.management import call_command

        root = User.objects.create_superuser("fresh-root", password="pw")
        call_command("bootstrap_plans", stdout=io.StringIO())
        self.assertTrue(Person.objects.get(user=root).communities.filter(slug="operators").exists())
        self.assertEqual(Subscription.objects.get(user=root).plan_key, "superuser")
        self.assertEqual(plan_for(root).key, "superuser")
        self.assertEqual(autoplan.assign_all(), 0)
        self.assertEqual(plan_for(root).key, "superuser")

    def test_assign_all_covers_every_account_once(self):
        self.person.communities.add(self.moderators)
        Subscription.objects.filter(user=self.mia).delete()        # as if made before the switch
        self.assertEqual(autoplan.assign_all(), 1)
        self.assertEqual(plan_for(self.mia).key, "team")
        self.assertEqual(autoplan.assign_all(), 0)

    def test_a_person_chooses_within_what_their_communities_offer(self):
        """The community is the ceiling, not the plan: a member put on Team
        by joining may step down to Free and stays there, through the sweep
        and through other membership changes, while Team is still offered."""
        self.person.communities.add(self.moderators)
        self.assertEqual(plan_for(self.mia).key, "team")
        client = Client()
        client.force_login(self.mia)
        page = client.get(reverse("subscriptions:plans"))
        self.assertContains(page, "Choose this plan")
        posted = client.post(reverse("subscriptions:subscribe", args=["free"]))
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(plan_for(self.mia).key, "free")
        self.assertEqual(autoplan.assign_all(), 0)                  # their choice stands
        self.person.communities.add(self.everyone)                  # another change: still stands
        self.assertEqual(plan_for(self.mia).key, "free")
        # Up again by choice, and a plan above the ceiling is unreachable.
        client.post(reverse("subscriptions:subscribe", args=["team"]))
        self.assertEqual(plan_for(self.mia).key, "team")
        self.person.communities.remove(self.moderators)
        self.assertEqual(plan_for(self.mia).key, "free")
        self.assertEqual(client.post(reverse("subscriptions:subscribe", args=["team"])).status_code, 404)


@override_settings(SUBSCRIPTION_AUTO_ASSIGN=False, SUBSCRIPTION_ENFORCEMENT=False)
class ChosenAsBeforeTests(TestCase):
    def test_without_the_switch_nothing_moves(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        moderators = Community.objects.create(name="Moderators", slug="moderators")
        CommunityPlanOffer.objects.create(community=moderators, plan_key="team")
        mia = User.objects.create_user("mia2", password="pw")
        Person.objects.create(user=mia, display_name="Mia").communities.add(moderators)
        self.assertFalse(autoplan.enabled())
        self.assertEqual(plan_for(mia).key, "free")
        client = Client()
        client.force_login(mia)
        self.assertContains(client.get(reverse("subscriptions:plans")), "Choose this plan")
