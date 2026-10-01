"""Clearances: what decides who reads, and nothing else (2026-09-28; a model
of their own since 2026-09-29).

A clearance (``socialhub.Clearance``) is hidden from members, given only by a
superuser, and grants no privilege; a community is untouched by all of it.
Each rule is asserted at every door it guards, because the failure this file
is written against is one door forgetting — a listing, an API, a form —
while the others hold.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_clearances
"""

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import (
    MAX_CLEARANCES,
    Clearance,
    Community,
    MembershipApplication,
    ReferenceRequest,
)

User = get_user_model()


def person(username, *communities, **flags):
    user = User.objects.create_user(username, f"{username}@example.com", "pw", **flags)
    someone = Person.objects.create(user=user, display_name=username.title())
    someone.communities.add(*communities)
    return someone


class ClearanceTestCase(TestCase):
    """A senior engineer: in `devs` (a community) and holding `internal` (a clearance)."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.senior = person("senior", cls.devs)
        cls.senior.clearances.add(cls.internal)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")
        Person.objects.create(user=cls.root, display_name="Root")


class OnThePlan:
    """The Clearances tab asks for the Superuser plan as well (2026-10-01,
    the review of stage 37c): `bootstrap_plans` puts every superuser there is
    on it. Only the classes that open the tab take it — it makes a community
    of its own, which the others count."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
            cls.root = User.objects.get(pk=cls.root.pk)


class ClearanceKindTests(ClearanceTestCase):
    def test_a_clearance_is_not_a_community(self):
        self.assertEqual(set(Clearance.objects.all()), {self.internal})
        self.assertEqual(set(Community.objects.all()), {self.devs})
        self.assertEqual(set(self.senior.clearances.all()), {self.internal})
        self.assertEqual(set(self.senior.communities.all()), {self.devs})
        self.assertEqual(set(self.internal.members.all()), {self.senior})


class ClearanceCapTests(ClearanceTestCase):
    """At most seven clearances on a platform (2026-09-28)."""

    def fill(self):
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"clearance{n}", slug=f"clearance{n}")

    def test_an_eighth_clearance_is_refused_by_clean_and_by_save(self):
        self.fill()
        eighth = Clearance(name="eighth", slug="eighth")
        with self.assertRaises(ValidationError) as caught:
            eighth.full_clean()
        self.assertIn("name", caught.exception.message_dict)
        with self.assertRaises(ValidationError):
            eighth.save()
        self.assertEqual(Clearance.objects.count(), MAX_CLEARANCES)

    def test_a_clearance_that_already_counts_saves_and_a_community_is_free(self):
        self.fill()
        self.internal.name = "internal docs"
        self.internal.save()                                      # its own save is never refused
        Community.objects.create(name="testers", slug="testers")  # a community: no cap


class ClearanceAdminMembershipTests(ClearanceTestCase):
    """Superusers put people in clearances — on the clearance, or on the person."""

    def _change_clearance(self, *members):
        return self.client.post(reverse("admin:socialhub_clearance_change", args=[self.internal.pk]), {
            "name": self.internal.name, "slug": self.internal.slug,
            "regen_security": "", "regen_compute": "", "regen_storage": "",
            "members": [member.pk for member in members],
        })

    def test_a_superuser_fills_a_clearance_on_its_own_page(self):
        self.client.force_login(self.root)
        newcomer = person("newcomer", self.devs)
        response = self._change_clearance(self.senior, newcomer)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.internal.members.all()), {self.senior, newcomer})

        self._change_clearance(newcomer)
        self.assertEqual(set(self.internal.members.all()), {newcomer})
        self.assertIn(self.devs, self.senior.communities.all())   # the community untouched

    def test_a_superuser_puts_a_person_in_a_clearance_on_the_person(self):
        self.client.force_login(self.root)
        junior = person("junior", self.devs)
        response = self.client.post(reverse("admin:people_person_change", args=[junior.pk]), {
            "user": junior.user_id, "display_name": junior.display_name, "slug": junior.slug,
            "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
            "location_sharing": "off", "preferred_language": "en",
            "communities": [self.devs.pk], "clearances": [self.internal.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(junior.communities.all()), {self.devs})
        self.assertEqual(set(junior.clearances.all()), {self.internal})

    def test_a_community_page_lists_no_members(self):
        self.client.force_login(self.root)
        community = self.client.get(reverse("admin:socialhub_community_change", args=[self.devs.pk]))
        clearance = self.client.get(reverse("admin:socialhub_clearance_change", args=[self.internal.pk]))
        self.assertNotIn("members", community.context["adminform"].form.fields)
        self.assertIn("members", clearance.context["adminform"].form.fields)


class ClearanceHiddenTests(ClearanceTestCase):
    """Every door that shows communities to a member shows no clearance — a
    clearance is not a community, so nothing lists it."""

    def test_the_directory(self):
        self.client.force_login(self.senior.user)
        names = {c.name for c in self.client.get("/socialhub/communities/").context["communities"]}
        self.assertEqual(names, {"devs"})

        self.client.force_login(self.root)
        response = self.client.get("/socialhub/communities/")
        self.assertEqual({c.name for c in response.context["communities"]}, {"devs"})
        # The Clearances tab: `root` here is a superuser off the Superuser plan
        # (no `bootstrap_plans`), whom it is not shown to since 37c.32; a host
        # that sells no plan shows it to the superuser bit alone.
        if apps.is_installed("toto.subscriptions"):
            self.assertNotContains(response, "fa-shield-halved")
        else:
            self.assertContains(response, "fa-shield-halved")

    def test_a_profile_shows_a_member_no_clearance(self):
        self.client.force_login(self.senior.user)
        page = self.client.get(reverse("socialhub:profile_details", args=[self.senior.slug]))
        self.assertEqual(list(page.context["profile"].listed_communities), [self.devs])
        roster = self.client.get(reverse("socialhub:profile_list"))
        for member in roster.context["profiles"]:
            self.assertNotIn("internal", {c.name for c in member.listed_communities})

        self.client.force_login(self.root)
        page = self.client.get(reverse("socialhub:profile_details", args=[self.senior.slug]))
        self.assertEqual(list(page.context["profile"].listed_communities), [self.devs])

    def test_the_api(self):
        self.client.force_login(add_to_mesh(self.senior.user))
        listed = self.client.get("/socialhub/api/communities/").json()["communities"]
        self.assertEqual([c["slug"] for c in listed], ["devs"])
        profile = self.client.get(f"/socialhub/api/profiles/{self.senior.slug}/").json()
        self.assertEqual([c["slug"] for c in profile["communities"]], ["devs"])
        self.assertEqual(profile["community_count"], 1)

    def test_the_connectors_read_what_a_member_sees(self):
        from toto.core.connectors import execute_connector_type

        listed = execute_connector_type("socialhub_read", {"resource": "community"}, {})
        self.assertEqual([c["slug"] for c in listed["data"]["communities"]], ["devs"])
        people = execute_connector_type("people_read", {}, {})["data"]["people"]
        chips = {c["slug"] for p in people for c in p["communities"]}
        self.assertEqual(chips, {"devs"})


class ApplicationTests(ClearanceTestCase):
    """The application flow is a community's door."""

    def _application(self, community, email="applicant@example.com"):
        User.objects.create(username=email.split("@")[0], email=email, is_active=False)
        return MembershipApplication.objects.create(
            email=email, community=community, code="424242", verified_at=timezone.now(),
            status="verified", expires_at=timezone.now() + timezone.timedelta(days=7))

    def test_the_form_offers_communities(self):
        form = self.client.get(reverse("socialhub:membership_application")).context["form"]
        self.assertEqual(set(form.fields["community"].queryset), {self.devs})

    def test_a_community_admits(self):
        application = self._application(self.devs)
        ref = ReferenceRequest.objects.create(application=application, referrer=self.senior)
        ref.status = "accepted"
        ref.save()
        applicant = self.devs.members.get(user__email="applicant@example.com")
        self.assertFalse(applicant.clearances.exists())            # admitted to nothing else


class ClearancesTabTests(OnThePlan, ClearanceTestCase):
    """The socialhub's Clearances tab (2026-09-29; a list with two doors since
    2026-09-30): superusers make a clearance — its speeds and its holders
    given at once — and remove one. Holders and speeds are changed in the
    admin, never here."""

    def as_(self, user):
        from django.test import Client

        client = Client()
        client.force_login(user)
        return client

    def add(self, **data):
        # One client, following the redirect: a refusal is Post/Redirect/Get,
        # and the list draws the draft from this session.
        if not hasattr(self, "_root_client"):
            self._root_client = self.as_(self.root)
        return self._root_client.post(reverse("socialhub:clearance_add"), data, follow=True)

    def test_the_tab_and_the_page_are_for_superusers(self):
        member = self.senior.user
        self.assertNotContains(self.as_(member).get(reverse("socialhub:community_list")),
                               'data-testid="tab-clearances"')
        self.assertEqual(self.as_(member).get(reverse("socialhub:clearances")).status_code, 403)
        page = self.as_(self.root).get(reverse("socialhub:clearances"))
        self.assertContains(page, 'data-testid="tab-clearances"')
        self.assertContains(page, 'data-testid="clearance-internal"')
        self.assertNotContains(page, 'data-testid="clearance-devs"')      # a community
        self.assertContains(self.as_(self.root).get(reverse("socialhub:profile_list")),
                            'data-testid="tab-clearances"')

    def test_making_a_clearance_and_the_cap(self):
        self.assertRedirects(self.add(name="confidential"), reverse("socialhub:clearances"),
                             fetch_redirect_response=False)
        self.assertTrue(Clearance.objects.filter(name="confidential").exists())
        for n in range(Clearance.objects.count(), MAX_CLEARANCES):
            Clearance.objects.create(name=f"c{n}", slug=f"c{n}")
        response = self.add(name="eighth")
        self.assertEqual(response.redirect_chain[-1][0], reverse("socialhub:clearances"))
        self.assertTrue(response.context["draft"]["open"])          # the modal re-opens on the list
        self.assertContains(response, "at most 7 clearances")
        self.assertContains(response, 'data-testid="clearances-full"')
        self.assertFalse(Clearance.objects.filter(name="eighth").exists())

    def test_holders_are_given_when_the_clearance_is_made(self):
        newcomer = person("newcomer")
        response = self.as_(self.root).post(reverse("socialhub:clearance_add"),
                                            {"name": "confidential", "person": [newcomer.pk]},
                                            follow=True)
        self.assertContains(response, "Clearance confidential made, held by 1.")
        confidential = Clearance.objects.get(name="confidential")
        self.assertEqual(set(confidential.members.all()), {newcomer})
        self.assertEqual(set(newcomer.communities.all()), set())    # a clearance, not a community

    def test_speeds_given_at_making_blank_means_the_pool_rate_and_bad_values_refused(self):
        from decimal import Decimal

        self.add(name="confidential", regen_security="8", regen_compute="12,5", regen_storage="")
        made = Clearance.objects.get(name="confidential")
        self.assertEqual(made.regen_security, Decimal("8"))
        self.assertEqual(made.regen_compute, Decimal("12.5"))
        self.assertIsNone(made.regen_storage)
        self.assertContains(self.add(name="payroll", regen_security="-1"),
                            "greater than or equal to 0")
        self.assertContains(self.add(name="payroll", regen_security="fast"), "is not a number")
        self.assertFalse(Clearance.objects.filter(name="payroll").exists())

    def test_the_removed_doors_are_gone(self):
        from django.urls import NoReverseMatch

        for name in ("clearance_member", "clearance_speeds"):
            with self.subTest(door=name), self.assertRaises(NoReverseMatch):
                reverse(f"socialhub:{name}", args=[self.internal.pk])

    def test_a_removed_clearance_and_one_still_in_use(self):
        from django.db.models import ProtectedError
        from unittest import mock

        spare = Clearance.objects.create(name="spare", slug="spare")
        self.as_(self.root).post(reverse("socialhub:clearance_delete", args=[spare.pk]))
        self.assertFalse(Clearance.objects.filter(pk=spare.pk).exists())
        with mock.patch.object(Clearance, "delete", side_effect=ProtectedError("in use", [])):
            response = self.as_(self.root).post(
                reverse("socialhub:clearance_delete", args=[self.internal.pk]), follow=True)
        self.assertContains(response, "still decides who reads")
        self.assertTrue(Clearance.objects.filter(pk=self.internal.pk).exists())

    def test_changes_reach_the_audit_chain(self):
        from decimal import Decimal

        from toto.audit.models import AuditRecord

        newcomer = person("newcomer")
        self.as_(self.root).post(reverse("socialhub:clearance_add"), {
            "name": "confidential", "regen_security": "8", "person": [newcomer.pk]})
        made = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_CREATED",
                                          metadata__clearance="confidential").get()
        self.assertEqual(made.actor_user, self.root)
        self.assertEqual({pool: Decimal(v) for pool, v in made.metadata["speeds"].items()},
                         {"security": Decimal("8")})
        added = AuditRecord.objects.filter(action="SOCIALHUB.CLEARANCE_MEMBER_ADDED",
                                          metadata__person=newcomer.slug).get()
        self.assertEqual(added.actor_user, self.root)
        self.assertEqual(added.metadata["clearance"], "confidential")


class OnlySuperusersMakeClearancesTests(OnThePlan, ClearanceTestCase):
    """Only a superuser makes a clearance (2026-09-29): the Clearances tab is theirs,
    and the admin's clearance pages refuse staff whatever rights they hold —
    staff make and change communities alone."""

    def staff(self):
        from django.contrib.auth.models import Permission

        user = User.objects.create_user("clerk", "c@example.com", "pw", is_staff=True)
        user.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="socialhub",
            content_type__model__in=("community", "clearance")))
        return user

    def test_the_clearances_tab_refuses_staff(self):
        from django.test import Client

        client = Client()
        client.force_login(self.staff())
        self.assertEqual(client.post(reverse("socialhub:clearance_add"),
                                     {"name": "confidential"}).status_code, 403)
        self.assertFalse(Clearance.objects.filter(name="confidential").exists())

    def test_the_admin_lets_staff_make_communities_only(self):
        from django.test import Client

        client = Client()
        client.force_login(self.staff())
        client.post(reverse("admin:socialhub_community_add"), {
            "name": "testers", "slug": "testers", "org_type": Community.OTHER,
            "privilege-TOTAL_FORMS": "0", "privilege-INITIAL_FORMS": "0",
            "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1"})
        self.assertTrue(Community.objects.filter(slug="testers").exists())
        # The clearance pages are shut to them, model permissions or not.
        for name, args in (("changelist", []), ("add", []), ("change", [self.internal.pk]),
                           ("delete", [self.internal.pk])):
            with self.subTest(page=name):
                response = client.get(reverse(f"admin:socialhub_clearance_{name}", args=args))
                self.assertIn(response.status_code, (302, 403))
        response = client.post(reverse("admin:socialhub_clearance_add"),
                               {"name": "confidential", "slug": "confidential"})
        self.assertIn(response.status_code, (302, 403))
        self.assertFalse(Clearance.objects.filter(name="confidential").exists())

    def test_a_superuser_still_can(self):
        from django.test import Client

        client = Client()
        client.force_login(self.root)
        self.assertEqual(client.get(reverse("admin:socialhub_clearance_changelist")).status_code, 200)
        self.assertEqual(client.get(reverse("admin:socialhub_clearance_change",
                                            args=[self.internal.pk])).status_code, 200)
        response = client.post(reverse("admin:socialhub_clearance_add"), {
            "name": "confidential", "slug": "confidential",
            "regen_security": "", "regen_compute": "", "regen_storage": ""})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Clearance.objects.filter(name="confidential").exists())
