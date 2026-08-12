"""Institutions grant; persons hold the union.

Two shapes of grant — a community to every member, a station to its one holder —
and one resolver. Run from a host with socialhub installed:

    python manage.py test toto.socialhub.tests_privileges
"""

from decimal import Decimal

from django.contrib.auth.models import AnonymousUser, User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from toto.people.models import Person
from toto.socialhub import privileges
from toto.socialhub.models import (
    Community,
    CommunityPrivilege,
    Constitution,
    ConstitutionSignature,
    Station,
)


def make_member(name, *communities):
    user = User.objects.create_user(name, f"{name}@example.com", "pw")
    person = Person.objects.create(user=user, display_name=name)
    for community in communities:
        person.communities.add(community)
    return user


def make_citizen(name, *communities):
    """A member who has signed a constitution — the office-holding qualification."""
    user = make_member(name, *communities)
    community = communities[0] if communities else Community.objects.create(
        name=f"{name}-home")
    constitution, _ = Constitution.objects.get_or_create(
        community=community, defaults={"title": "Charter", "body": "Be good."})
    # signed_at is nullable and stays empty until the person actually signs —
    # a pending signature is not citizenship.
    ConstitutionSignature.objects.create(
        constitution=constitution, person=user.community_profile,
        signed_at=timezone.now())
    return user


class UnionTests(TestCase):
    """Any institution granting a right grants it. Highest privilege always."""

    @classmethod
    def setUpTestData(cls):
        cls.plain = Community.objects.create(name="Weavers")
        cls.agents = Community.objects.create(name="Agents")
        CommunityPrivilege.objects.create(
            community=cls.agents,
            may_see_community_chain=True,
            may_administer_communities=True,
        )

    def test_membership_is_the_grant(self):
        outsider = make_member("outsider", self.plain)
        insider = make_member("insider", self.plain, self.agents)

        self.assertFalse(privileges.has_privilege(outsider, "may_see_community_chain"))
        self.assertTrue(privileges.has_privilege(insider, "may_see_community_chain"))

    def test_expulsion_is_the_revocation(self):
        user = make_member("temp", self.agents)
        self.assertTrue(privileges.has_privilege(user, "may_administer_communities"))

        user.community_profile.communities.remove(self.agents)
        self.assertFalse(privileges.has_privilege(user, "may_administer_communities"))

    def test_an_office_grants_what_no_community_of_theirs_does(self):
        user = make_citizen("archivist", self.plain)
        self.assertFalse(privileges.has_privilege(user, "may_administer_communities"))

        station = Station.objects.create(
            name="Archivist", holder=user.community_profile,
            may_administer_communities=True)

        self.assertTrue(privileges.has_privilege(user, "may_administer_communities"))

        # Vacating revokes, exactly as leaving a community does — the right
        # belongs to the office, and the office outlives the holder.
        station.holder = None
        station.save(update_fields=["holder"])
        self.assertFalse(privileges.has_privilege(user, "may_administer_communities"))

    def test_a_deactivated_office_grants_nothing(self):
        user = make_citizen("suspended", self.plain)
        Station.objects.create(name="Dormant office", holder=user.community_profile,
                               may_operate_mint=True, active=False)
        self.assertFalse(privileges.has_privilege(user, "may_operate_mint"))

    def test_a_community_without_a_row_grants_nothing(self):
        user = make_member("commoner", self.plain)
        for right in privileges.RIGHTS:
            self.assertFalse(privileges.has_privilege(user, right), right)

    def test_a_mistyped_right_raises_rather_than_refusing_forever(self):
        # A typo that silently returns False is a gate nobody can pass and
        # nobody can find. Raising is the kindness here.
        user = make_member("anyone", self.plain)
        with self.assertRaises(ValueError):
            privileges.has_privilege(user, "may_fly")

    def test_anonymous_and_userless_degrade_to_the_commoner(self):
        self.assertFalse(privileges.has_privilege(AnonymousUser(),
                                                  "may_see_community_chain"))
        self.assertFalse(privileges.has_privilege(None, "may_see_community_chain"))
        self.assertEqual(privileges.head_weight_for(None),
                         privileges.ORDINARY_HEAD_WEIGHT)
        self.assertEqual(privileges.limit_multiplier_for(None), Decimal("1"))


class HeadWeightTests(TestCase):
    """The LOWEST weight wins: a good community is what makes you cheap to tax."""

    @classmethod
    def setUpTestData(cls):
        cls.trusted = Community.objects.create(name="Trusted")
        CommunityPrivilege.objects.create(community=cls.trusted,
                                          head_weight=Decimal("0"))
        cls.suspect = Community.objects.create(name="Suspect")
        CommunityPrivilege.objects.create(community=cls.suspect,
                                          head_weight=Decimal("3"))
        cls.plain = Community.objects.create(name="Plain")

    def test_the_best_community_wins(self):
        user = make_member("both", self.suspect, self.trusted)
        self.assertEqual(privileges.head_weight_for(user), Decimal("0"))

    def test_a_suspect_community_alone_is_paid_for(self):
        user = make_member("suspect-only", self.suspect)
        self.assertEqual(privileges.head_weight_for(user), Decimal("3"))

    def test_a_plain_community_caps_you_at_the_ordinary_rate(self):
        # Belonging to an ordinary community is itself a way out of a heavy
        # one — the lowest weight wins, and a community with no row is a 1.
        user = make_member("mixed", self.suspect, self.plain)
        self.assertEqual(privileges.head_weight_for(user), Decimal("1"))

    def test_no_community_at_all_pays_the_ordinary_rate(self):
        # A tax on everyone, not a penalty for being unaffiliated.
        user = make_member("hermit")
        self.assertEqual(privileges.head_weight_for(user), Decimal("1"))

    def test_an_office_does_not_change_what_you_owe(self):
        """Extra limits, same taxes — the constraint, asserted directly."""
        user = make_citizen("officer", self.suspect)
        Station.objects.create(name="Someone important",
                               holder=user.community_profile,
                               limit_multiplier=Decimal("10"))
        self.assertEqual(privileges.head_weight_for(user), Decimal("3"))


class BulkHeadWeightTests(TestCase):
    """The nightly run resolves a whole host in a fixed number of queries."""

    @classmethod
    def setUpTestData(cls):
        cls.trusted = Community.objects.create(name="Trusted")
        CommunityPrivilege.objects.create(community=cls.trusted,
                                          head_weight=Decimal("0"))
        cls.suspect = Community.objects.create(name="Suspect")
        CommunityPrivilege.objects.create(community=cls.suspect,
                                          head_weight=Decimal("3"))
        cls.plain = Community.objects.create(name="Plain")

    def test_bulk_agrees_with_single(self):
        users = [
            make_member("b-trusted", self.trusted),
            make_member("b-suspect", self.suspect),
            make_member("b-plain", self.plain),
            make_member("b-both", self.trusted, self.suspect),
            make_member("b-hermit"),
        ]
        ids = [u.pk for u in users]
        bulk = privileges.head_weights_for_users(ids)

        for user in users:
            expected = privileges.head_weight_for(user)
            got = bulk.get(user.pk, privileges.ORDINARY_HEAD_WEIGHT)
            self.assertEqual(got, expected, user.username)

    def test_only_the_differing_ids_appear(self):
        plain = make_member("only-plain", self.plain)
        suspect = make_member("only-suspect", self.suspect)

        bulk = privileges.head_weights_for_users([plain.pk, suspect.pk])

        self.assertEqual(bulk, {suspect.pk: Decimal("3")})

    def test_the_query_count_does_not_grow_with_the_host(self):
        few = [make_member(f"few-{n}", self.suspect).pk for n in range(2)]
        many = [make_member(f"many-{n}", self.suspect).pk for n in range(30)]

        with self.assertNumQueries(2):
            privileges.head_weights_for_users(few)
        with self.assertNumQueries(2):
            privileges.head_weights_for_users(many)

    def test_no_weighted_community_costs_one_query_and_no_rows(self):
        ids = [make_member(f"p-{n}", self.plain).pk for n in range(3)]
        CommunityPrivilege.objects.all().delete()

        with self.assertNumQueries(1):
            self.assertEqual(privileges.head_weights_for_users(ids), {})


class LimitMultiplierTests(TestCase):
    def setUp(self):
        self.community = Community.objects.create(name="Guild")

    def test_the_largest_office_wins_and_never_shrinks_the_limit(self):
        user = make_citizen("busy", self.community)
        Station.objects.create(name="Small office", holder=user.community_profile,
                               limit_multiplier=Decimal("2"))
        Station.objects.create(name="Big office", holder=user.community_profile,
                               limit_multiplier=Decimal("10"))

        self.assertEqual(privileges.limit_multiplier_for(user), Decimal("10"))

    def test_an_office_can_never_reduce_headroom(self):
        # A multiplier below 1 would make an office a punishment. Refuse it at
        # the read, so a mistyped admin value cannot lock someone out.
        user = make_citizen("shrunk", self.community)
        Station.objects.create(name="Odd office", holder=user.community_profile,
                               limit_multiplier=Decimal("0.5"))

        self.assertEqual(privileges.limit_multiplier_for(user), Decimal("1"))

    def test_no_office_is_exactly_one(self):
        user = make_member("plain", self.community)
        self.assertEqual(privileges.limit_multiplier_for(user), Decimal("1"))


class StationIntegrityTests(TestCase):
    """An office is public and persistent; who may hold it is guarded."""

    def setUp(self):
        self.community = Community.objects.create(name="Guild")

    def test_only_a_committed_citizen_may_hold_an_office(self):
        stranger = make_member("stranger", self.community)
        station = Station(name="Warden", holder=stranger.community_profile)

        with self.assertRaises(ValidationError) as caught:
            station.full_clean()
        self.assertIn("holder", caught.exception.error_dict)

        # Signing the constitution is what qualifies them — the promise
        # people/civic.py has documented all along.
        citizen = make_citizen("citizen", self.community)
        Station(name="Warden", holder=citizen.community_profile).full_clean()

    def test_a_paid_office_needs_a_holder_with_a_login(self):
        # Person.user is nullable and nothing creates a Person on signup, so a
        # loginless Person is an ordinary row — and a signature hangs off the
        # Person, so they can be a full citizen with no way to log in. What
        # they cannot be is PAID: there is no billing account to credit.
        person = Person.objects.create(display_name="No login")
        constitution = Constitution.objects.create(
            community=self.community, title="Charter", body="Be good.")
        ConstitutionSignature.objects.create(
            constitution=constitution, person=person, signed_at=timezone.now())

        station = Station(name="Treasurer", holder=person, stipend=Decimal("1"))
        with self.assertRaises(ValidationError) as caught:
            station.full_clean()
        self.assertIn("login", str(caught.exception))

        station.stipend = Decimal("0")
        station.full_clean()      # an unpaid office is fine

    def test_a_vacant_office_still_exists(self):
        station = Station.objects.create(name="Archivist", charter="Keeps the books.")
        station.full_clean()
        self.assertIsNone(station.holder)
        self.assertEqual(Station.objects.filter(active=True).count(), 1)

    def test_serves_is_attribution_and_the_office_survives_its_community(self):
        citizen = make_citizen("local", self.community)
        station = Station.objects.create(
            name="Master of the Guild", holder=citizen.community_profile,
            serves=self.community)

        self.community.delete()
        station.refresh_from_db()

        # The office is federal: losing the community it served does not
        # abolish it, and never could have — nobody's payroll depended on it.
        self.assertIsNone(station.serves)
        self.assertTrue(Station.objects.filter(pk=station.pk).exists())


class RosterTests(TestCase):
    """Public roster: the office is visible, what it grants and pays is not."""

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.guild = Community.objects.create(name="Weavers")

    def test_the_roster_needs_no_login_and_names_the_federal_payer(self):
        from django.urls import reverse

        holder = make_citizen("archivist", self.guild)
        Station.objects.create(
            name="Archivist", charter="Keeps the books.",
            holder=holder.community_profile, serves=self.guild,
            limit_multiplier=Decimal("10"), stipend=Decimal("7.5"),
            may_operate_mint=True)

        response = self.client.get(reverse("socialhub:station_list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Archivist")
        self.assertContains(response, "Keeps the books.")
        self.assertContains(response, "Paid by the federal treasury")

        # An office serving a guild is the one a reader would most likely
        # assume the guild pays for. It does not, and the page says so.
        self.assertNotContains(response, "Paid by Weavers")

    def test_capabilities_and_pay_never_reach_the_page(self):
        from django.urls import reverse

        holder = make_citizen("treasurer", self.guild)
        Station.objects.create(
            name="Treasurer", holder=holder.community_profile,
            limit_multiplier=Decimal("10"), stipend=Decimal("7.5"),
            may_operate_mint=True, may_administer_communities=True)

        body = self.client.get(reverse("socialhub:station_list")).content.decode()

        for secret in ("7.5", "may_operate_mint", "may_administer_communities",
                       "limit_multiplier"):
            self.assertNotIn(secret, body, secret)

    def test_a_vacant_office_is_listed_as_vacant(self):
        from django.urls import reverse

        Station.objects.create(name="Warden", charter="Watches the gate.")

        response = self.client.get(reverse("socialhub:station_list"))

        self.assertContains(response, "Warden")
        self.assertContains(response, "Vacant")

    def test_a_deactivated_office_is_not_listed(self):
        from django.urls import reverse

        Station.objects.create(name="Abolished office", active=False)

        response = self.client.get(reverse("socialhub:station_list"))

        self.assertNotContains(response, "Abolished office")
