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


class ProfileOfficeTests(TestCase):
    """The profile lists the offices a person holds — and only their own pay.

    The roster's privacy is pinned above, but that test targets
    `socialhub:station_list` alone, so it would keep passing while this page
    leaked. The mirror below is what actually guards the profile.
    """

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.guild = Community.objects.create(name="Weavers")
        cls.holder = make_citizen("archivist", cls.guild)
        cls.onlooker = make_member("onlooker", cls.guild)
        cls.station = Station.objects.create(
            name="Archivist", charter="Keeps the books.",
            holder=cls.holder.community_profile, serves=cls.guild,
            since=timezone.now().date(),
            limit_multiplier=Decimal("10"), stipend=Decimal("7.5"),
            may_operate_mint=True, may_administer_communities=True)

    def _get(self, as_user, person=None):
        from django.urls import reverse

        self.client.force_login(as_user)
        person = person or self.holder.community_profile
        return self.client.get(
            reverse("socialhub:profile_details", args=[person.slug]))

    def test_the_office_appears_on_the_holders_profile(self):
        response = self._get(self.onlooker)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Archivist")
        self.assertContains(response, "Keeps the books.")
        # The fact a reader is most likely to get wrong, worded as the roster
        # words it so the two surfaces cannot drift.
        self.assertContains(response, "Paid by the federal treasury")

    def test_another_persons_pay_and_powers_never_reach_the_page(self):
        body = self._get(self.onlooker).content.decode()

        for secret in ("7.5", "may_operate_mint", "may_administer_communities",
                       "limit_multiplier"):
            self.assertNotIn(secret, body, secret)

    def test_a_holder_sees_their_own_stipend(self):
        from unittest.mock import patch

        from toto.quota import rates

        with patch.object(rates, "pricing_enabled", return_value=True), \
             patch.object(rates, "price_asset_symbol", return_value="ASR"):
            body = self._get(self.holder).content.decode()

        self.assertIn("7.5", body)
        self.assertIn("ASR", body)

    def test_the_multiplier_stays_hidden_even_from_its_own_holder(self):
        """The carve-out is the stipend and nothing else."""
        from unittest.mock import patch

        from toto.quota import rates

        with patch.object(rates, "pricing_enabled", return_value=True), \
             patch.object(rates, "price_asset_symbol", return_value="ASR"):
            body = self._get(self.holder).content.decode()

        for secret in ("limit_multiplier", "may_operate_mint"):
            self.assertNotIn(secret, body, secret)

    def test_an_unbilled_host_shows_the_office_and_no_amount(self):
        """aurelian pins no economy wheel at all; the office is still real."""
        from unittest.mock import patch

        from toto.quota import rates

        with patch.object(rates, "pricing_enabled", return_value=False):
            body = self._get(self.holder).content.decode()

        self.assertIn("Archivist", body)
        self.assertNotIn("7.5", body)

    def test_a_person_holding_no_office_gets_no_offices_section(self):
        plain = make_member("nobody", self.guild)
        body = self._get(plain, person=plain.community_profile).content.decode()
        self.assertNotIn("Offices", body)

    def test_a_vacated_office_leaves_the_profile(self):
        self.station.holder = None
        self.station.save(update_fields=["holder"])
        body = self._get(self.onlooker).content.decode()
        self.assertNotIn("Archivist", body)

    def test_a_deactivated_office_is_not_shown(self):
        self.station.active = False
        self.station.save(update_fields=["active"])
        body = self._get(self.onlooker).content.decode()
        self.assertNotIn("Archivist", body)

    def test_a_second_office_costs_no_extra_query(self):
        """The Prefetch works: offices do not cost one query each."""
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.urls import reverse

        self.client.force_login(self.onlooker)
        url = reverse("socialhub:profile_details",
                      args=[self.holder.community_profile.slug])
        self.client.get(url)                       # warm session and caches

        with CaptureQueriesContext(connection) as one_office:
            self.client.get(url)

        Station.objects.create(
            name="Second office", charter="Also real.",
            holder=self.holder.community_profile, serves=self.guild)

        with CaptureQueriesContext(connection) as two_offices:
            self.client.get(url)

        self.assertEqual(
            len(two_offices.captured_queries), len(one_office.captured_queries),
            "a second office cost extra queries — the Prefetch is not in effect")

    def test_the_profile_row_is_fetched_once(self):
        """get_context_data must read `self.object`, not call get_object().

        get_object() re-runs the whole queryset — prefetches included — so the
        page still renders correctly and the N+1 test above still passes. What
        it costs is a second full fetch of the person and every prefetch on
        every request, which is invisible until it is measured.
        """
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from django.urls import reverse

        self.client.force_login(self.onlooker)
        url = reverse("socialhub:profile_details",
                      args=[self.holder.community_profile.slug])
        self.client.get(url)                       # warm session and caches

        with CaptureQueriesContext(connection) as captured:
            self.client.get(url)

        # Narrowed to the SLUG lookup on purpose. The page chrome fetches the
        # REQUESTER's own person by user_id as well, which is a different query
        # and not the one this test is about.
        table = Person._meta.db_table
        fetches = [q for q in captured.captured_queries
                   if f'FROM "{table}"' in q["sql"] and '"slug" =' in q["sql"]]
        self.assertEqual(
            len(fetches), 1,
            f"the viewed profile was fetched {len(fetches)} times; "
            f"get_context_data is re-running the queryset")


class HeadTaxPanelTests(TestCase):
    """The head tax, announced on the profile through the plugin registry.

    The wording is the substance here. A community never pays and never
    receives; its members do. A panel that said otherwise would teach exactly
    the separatism the design refuses.
    """

    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        cls.trusted = Community.objects.create(name="Trusted Guild")
        cls.watched = Community.objects.create(name="Watched Circle")
        CommunityPrivilege.objects.create(
            community=cls.trusted, head_weight=Decimal("1"))
        CommunityPrivilege.objects.create(
            community=cls.watched, head_weight=Decimal("3"))
        cls.member = make_member("taxed", cls.watched)

    def _get(self, person=None, as_user=None):
        from django.urls import reverse

        self.client.force_login(as_user or self.member)
        person = person or self.member.community_profile
        return self.client.get(
            reverse("socialhub:profile_details", args=[person.slug]))

    def _with_levy(self, active=True):
        """The head tax exists only where a levy engine registered its provider."""
        from unittest.mock import patch

        from toto.quota import levies

        return patch.object(levies, "of", return_value={
            "code": "civics.head", "metric_code": "civics.head",
            "active": active, "has_rule": True})

    def test_the_panel_announces_the_rate(self):
        with self._with_levy():
            body = self._get().content.decode()

        self.assertIn("Head tax", body)
        self.assertIn("Watched Circle", body)
        self.assertIn("3", body)

    def test_it_says_members_pay_and_never_that_the_community_does(self):
        with self._with_levy():
            body = self._get().content.decode()

        self.assertIn("members pay more than ordinary", body)
        # The sentence the whole design exists to prevent.
        self.assertNotIn("this community pays", body.lower())

    def test_a_second_better_community_lowers_the_effective_rate(self):
        """The lowest weight wins — belonging to a good community is the way out."""
        from toto.socialhub.privileges import head_weight_for

        self.member.community_profile.communities.add(self.trusted)

        self.assertEqual(head_weight_for(self.member), Decimal("1"))
        with self._with_levy():
            body = self._get().content.decode()
        self.assertIn("Trusted Guild", body)
        self.assertIn("members pay the ordinary rate", body)

    def test_an_exempt_community_says_so(self):
        CommunityPrivilege.objects.filter(community=self.watched).update(
            head_weight=Decimal("0"))
        with self._with_levy():
            body = self._get().content.decode()
        self.assertIn("members pay nothing", body)

    def test_a_community_with_no_privilege_row_is_ordinary_not_free(self):
        """A missing row is the commoner default, not an exemption."""
        plain = Community.objects.create(name="Plain Company")
        person = make_member("plainly", plain)

        with self._with_levy():
            body = self._get(person=person.community_profile,
                             as_user=person).content.decode()

        self.assertIn("members pay the ordinary rate", body)
        self.assertNotIn("members pay nothing", body)

    def test_an_unarmed_levy_says_it_is_not_being_collected(self):
        with self._with_levy(active=False):
            body = self._get().content.decode()
        self.assertIn("not currently being levied", body)

    def test_a_host_with_no_levy_engine_shows_no_panel(self):
        """aurelian and placidia never register the provider at all."""
        from unittest.mock import patch

        from toto.quota import levies

        with patch.object(levies, "of", return_value=None):
            body = self._get().content.decode()

        self.assertNotIn("Head tax", body)

    def test_a_person_in_no_community_gets_no_panel(self):
        loner = make_member("loner")
        with self._with_levy():
            body = self._get(person=loner.community_profile,
                             as_user=loner).content.decode()
        self.assertNotIn("Head tax", body)

    def test_the_other_privileges_still_never_render(self):
        """The carve-out is head_weight alone; the rights stay admin-only."""
        CommunityPrivilege.objects.filter(community=self.watched).update(
            may_operate_mint=True, may_administer_communities=True)

        with self._with_levy():
            body = self._get().content.decode()

        for secret in ("may_operate_mint", "may_administer_communities",
                       "may_see_community_chain"):
            self.assertNotIn(secret, body, secret)
