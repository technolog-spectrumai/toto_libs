"""Circles: communities that decide who reads, and nothing else (2026-09-28).

A circle (`Community.is_circle`) is hidden from members, joined only through
the admin, and grants no privilege; a functional community is untouched by all
of it. Each rule is asserted at every door it guards, because the failure this
file is written against is one door forgetting — a listing, an API, a form —
while the others hold. What the money axis refuses (plan offers, discounts)
is asserted in `toto.subscriptions.tests_eligibility`.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_circles
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import privileges
from toto.socialhub.models import (
    MAX_CIRCLES,
    Community,
    CommunityNewsPost,
    CommunityPrivilege,
    MembershipApplication,
    ReferenceRequest,
)

User = get_user_model()


def person(username, *communities, **flags):
    user = User.objects.create_user(username, f"{username}@example.com", "pw", **flags)
    someone = Person.objects.create(user=user, display_name=username.title())
    someone.communities.add(*communities)
    return someone


class CircleTestCase(TestCase):
    """A senior engineer: in `devs` (functional) and `seniors` (a circle)."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.devs = Community.objects.create(name="devs", slug="devs")
        cls.seniors = Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        cls.senior = person("senior", cls.devs, cls.seniors)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")
        Person.objects.create(user=cls.root, display_name="Root")


class CircleKindTests(CircleTestCase):
    def test_a_community_is_functional_unless_made_a_circle(self):
        self.assertFalse(Community.objects.create(name="testers").is_circle)
        self.assertEqual(set(Community.objects.circles()), {self.seniors})
        self.assertNotIn(self.seniors, Community.objects.functional())

    def test_only_a_superuser_is_shown_circles(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        for viewer in (self.senior.user, staff, AnonymousUser()):
            self.assertEqual(set(Community.objects.listed_for(viewer)), {self.devs})
        self.assertEqual(set(Community.objects.listed_for(self.root)),
                         {self.devs, self.seniors})

    def test_a_circle_stands_alone(self):
        with self.assertRaises(ValidationError) as caught:
            Community(name="board", is_circle=True, parent=self.devs).full_clean()
        self.assertIn("parent", caught.exception.message_dict)
        with self.assertRaises(ValidationError) as caught:
            Community(name="juniors", parent=self.seniors).full_clean()
        self.assertIn("parent", caught.exception.message_dict)
        Community.objects.create(name="devs-backend", parent=self.devs)
        self.devs.is_circle = True
        with self.assertRaises(ValidationError) as caught:
            self.devs.full_clean()
        self.assertIn("is_circle", caught.exception.message_dict)

    def test_a_community_holding_a_privilege_cannot_become_a_circle(self):
        CommunityPrivilege.objects.create(community=self.devs, may_see_community_chain=True)
        self.devs.is_circle = True
        with self.assertRaises(ValidationError) as caught:
            self.devs.full_clean()
        self.assertIn("is_circle", caught.exception.message_dict)


class CircleCapTests(CircleTestCase):
    """At most seven circles on a platform (2026-09-28)."""

    def fill(self):
        for n in range(Community.objects.circles().count(), MAX_CIRCLES):
            Community.objects.create(name=f"circle{n}", slug=f"circle{n}", is_circle=True)

    def test_an_eighth_circle_is_refused_by_clean_and_by_save(self):
        self.fill()
        eighth = Community(name="eighth", slug="eighth", is_circle=True)
        with self.assertRaises(ValidationError):
            eighth.full_clean()
        with self.assertRaises(ValidationError):
            eighth.save()
        self.assertEqual(Community.objects.circles().count(), MAX_CIRCLES)

    def test_a_community_cannot_become_a_circle_at_the_cap(self):
        self.fill()
        self.devs.is_circle = True
        with self.assertRaises(ValidationError):
            self.devs.save()

    def test_a_circle_that_already_counts_saves_and_a_functional_one_is_free(self):
        self.fill()
        self.seniors.name = "the seniors"
        self.seniors.save()                                       # its own save is never refused
        Community.objects.create(name="testers", slug="testers")  # functional: no cap


class CirclePrivilegeTests(CircleTestCase):
    def test_a_circle_cannot_be_given_a_privilege(self):
        with self.assertRaises(ValidationError):
            CommunityPrivilege.objects.create(community=self.seniors, may_see_community_chain=True)
        with self.assertRaises(ValidationError):
            CommunityPrivilege(community=self.seniors).full_clean()
        self.assertFalse(CommunityPrivilege.objects.filter(community=self.seniors).exists())

    def test_a_privilege_left_on_a_circle_grants_nothing(self):
        """A row that predates the flip, or was written past the model."""
        board = Community.objects.create(name="board")
        CommunityPrivilege.objects.create(community=board, may_see_community_chain=True)
        cto = person("cto", self.devs, board)
        self.assertTrue(privileges.has_privilege(cto.user, "may_see_community_chain"))

        Community.objects.filter(pk=board.pk).update(is_circle=True)
        self.assertFalse(privileges.has_privilege(cto.user, "may_see_community_chain"))

    def test_the_admin_offers_a_circle_no_grant(self):
        self.client.force_login(self.root)
        circle = self.client.get(reverse("admin:socialhub_community_change", args=[self.seniors.pk]))
        functional = self.client.get(reverse("admin:socialhub_community_change", args=[self.devs.pk]))
        self.assertEqual(len(circle.context["inline_admin_formsets"]), 0)
        self.assertEqual(len(functional.context["inline_admin_formsets"]), 1)

    def test_the_admin_refuses_a_new_circle_with_a_grant(self):
        self.client.force_login(self.root)
        response = self.client.post(reverse("admin:socialhub_community_add"), {
            "name": "board", "slug": "board", "org_type": Community.OTHER, "is_circle": "on",
            "privilege-TOTAL_FORMS": "1", "privilege-INITIAL_FORMS": "0",
            "privilege-MIN_NUM_FORMS": "0", "privilege-MAX_NUM_FORMS": "1",
            "privilege-0-may_see_community_chain": "on",
        })
        self.assertEqual(response.status_code, 200)       # re-rendered with the error
        self.assertFalse(Community.objects.filter(slug="board").exists())
        self.assertFalse(CommunityPrivilege.objects.exists())


class CircleAdminMembershipTests(CircleTestCase):
    """Superusers put people in circles — on the circle, or on the person."""

    def _change_circle(self, *members):
        return self.client.post(reverse("admin:socialhub_community_change", args=[self.seniors.pk]), {
            "name": self.seniors.name, "slug": self.seniors.slug,
            "org_type": self.seniors.org_type, "is_circle": "on",
            "members": [member.pk for member in members],
        })

    def test_a_superuser_fills_a_circle_on_its_own_page(self):
        self.client.force_login(self.root)
        newcomer = person("newcomer", self.devs)
        response = self._change_circle(self.senior, newcomer)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(self.seniors.members.all()), {self.senior, newcomer})

        self._change_circle(newcomer)
        self.assertEqual(set(self.seniors.members.all()), {newcomer})
        self.assertIn(self.devs, self.senior.communities.all())   # functional untouched

    def test_a_superuser_puts_a_person_in_a_circle_on_the_person(self):
        self.client.force_login(self.root)
        junior = person("junior", self.devs)
        response = self.client.post(reverse("admin:people_person_change", args=[junior.pk]), {
            "user": junior.user_id, "display_name": junior.display_name, "slug": junior.slug,
            "joined_date_0": "2026-09-28", "joined_date_1": "10:00:00",
            "location_sharing": "off", "preferred_language": "en",
            "communities": [self.devs.pk, self.seniors.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(set(junior.communities.all()), {self.devs, self.seniors})

    def test_a_functional_community_page_lists_no_members(self):
        self.client.force_login(self.root)
        functional = self.client.get(reverse("admin:socialhub_community_change", args=[self.devs.pk]))
        circle = self.client.get(reverse("admin:socialhub_community_change", args=[self.seniors.pk]))
        self.assertNotIn("members", functional.context["adminform"].form.fields)
        self.assertIn("members", circle.context["adminform"].form.fields)


class CircleHiddenTests(CircleTestCase):
    """Every door that shows communities to a member shows no circle."""

    def test_the_directory(self):
        self.client.force_login(self.senior.user)
        names = {c.name for c in self.client.get("/socialhub/communities/").context["communities"]}
        self.assertEqual(names, {"devs"})

        self.client.force_login(self.root)
        response = self.client.get("/socialhub/communities/")
        self.assertEqual({c.name for c in response.context["communities"]}, {"devs", "seniors"})
        self.assertContains(response, "fa-circle-nodes")

    def test_the_page_of_a_circle_is_a_404_even_to_its_member(self):
        self.client.force_login(self.senior.user)
        self.assertEqual(self.client.get("/socialhub/communities/seniors/").status_code, 404)
        self.assertEqual(self.client.get("/socialhub/communities/devs/").status_code, 200)
        self.assertEqual(self.client.get(
            "/socialhub/community/org-chart/data/seniors/").status_code, 404)

        self.client.force_login(self.root)
        self.assertEqual(self.client.get("/socialhub/communities/seniors/").status_code, 200)

    def test_the_chain_and_administrata(self):
        grantor = Community.objects.create(name="federal-agents")
        CommunityPrivilege.objects.create(community=grantor, may_see_community_chain=True,
                                          may_administer_communities=True)
        self.senior.communities.add(grantor)
        self.client.force_login(self.senior.user)
        graph = self.client.get("/socialhub/communities/devs/administrata/graph.json").json()
        self.assertNotIn(f"c-{self.seniors.pk}", {node["id"] for node in graph["nodes"]})
        self.assertEqual(self.client.get(
            "/socialhub/communities/seniors/administrata/").status_code, 404)

    def test_a_profile_shows_a_member_no_circle(self):
        self.client.force_login(self.senior.user)
        page = self.client.get(reverse("socialhub:profile_details", args=[self.senior.slug]))
        self.assertEqual(list(page.context["profile"].listed_communities), [self.devs])
        roster = self.client.get(reverse("socialhub:profile_list"))
        for member in roster.context["profiles"]:
            self.assertNotIn(self.seniors, member.listed_communities)

        self.client.force_login(self.root)
        page = self.client.get(reverse("socialhub:profile_details", args=[self.senior.slug]))
        self.assertEqual(list(page.context["profile"].listed_communities), [self.devs, self.seniors])

    def test_the_api(self):
        self.client.force_login(add_to_mesh(self.senior.user))
        listed = self.client.get("/socialhub/api/communities/").json()["communities"]
        self.assertEqual([c["slug"] for c in listed], ["devs"])
        self.assertEqual(self.client.get("/socialhub/api/communities/seniors/").status_code, 404)
        self.assertEqual(self.client.get(
            "/socialhub/api/communities/seniors/org-chart/").status_code, 404)
        profile = self.client.get(f"/socialhub/api/profiles/{self.senior.slug}/").json()
        self.assertEqual([c["slug"] for c in profile["communities"]], ["devs"])
        self.assertEqual(profile["community_count"], 1)

    def test_a_circle_has_no_news_door_for_a_member(self):
        staff = User.objects.create_user("editor", password="pw", is_staff=True)
        post = CommunityNewsPost.objects.create(community=self.seniors, title="Board minutes",
                                                content="<p>x</p>")
        self.client.force_login(staff)            # staff may manage any community's news
        self.assertEqual(self.client.get("/socialhub/communities/seniors/news/new/").status_code, 404)
        self.assertEqual(self.client.get(f"/socialhub/community-news/{post.pk}/edit/").status_code, 404)
        self.assertEqual(self.client.get(f"/socialhub/community-news/{post.pk}/delete/").status_code, 404)
        self.assertEqual(self.client.get("/socialhub/communities/devs/news/new/").status_code, 200)

    def test_the_connectors_read_what_a_member_sees(self):
        from toto.core.connectors import ConnectorExecutionError, execute_connector_type

        listed = execute_connector_type("socialhub_read", {"resource": "community"}, {})
        self.assertEqual([c["slug"] for c in listed["data"]["communities"]], ["devs"])
        with self.assertRaises(ConnectorExecutionError):
            execute_connector_type("socialhub_read", {"resource": "community", "action": "get",
                                                      "slug": "seniors"}, {})
        people = execute_connector_type("people_read", {}, {})["data"]["people"]
        chips = {c["slug"] for p in people for c in p["communities"]}
        self.assertEqual(chips, {"devs"})
        members = execute_connector_type("people_read", {"community_slug": "seniors"}, {})
        self.assertEqual(members["data"]["people"], [])


class CircleNotJoinableTests(CircleTestCase):
    """The application flow is a functional community's door, never a circle's."""

    def _application(self, community, email="applicant@example.com"):
        User.objects.create(username=email.split("@")[0], email=email, is_active=False)
        return MembershipApplication.objects.create(
            email=email, community=community, code="424242", verified_at=timezone.now(),
            status="verified", expires_at=timezone.now() + timezone.timedelta(days=7))

    def test_the_form_offers_no_circle(self):
        form = self.client.get(reverse("socialhub:membership_application")).context["form"]
        self.assertEqual(set(form.fields["community"].queryset), {self.devs})

    def test_posting_a_circle_is_refused(self):
        response = self.client.post(reverse("socialhub:membership_application"), {
            "username": "outsider", "email": "outsider@example.com",
            "community": self.seniors.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(MembershipApplication.objects.exists())
        self.assertFalse(User.objects.filter(username="outsider").exists())

    def test_the_model_refuses_an_application_to_a_circle(self):
        application = MembershipApplication(
            email="x@example.com", community=self.seniors,
            expires_at=timezone.now() + timezone.timedelta(days=7))
        with self.assertRaises(ValidationError) as caught:
            application.full_clean()
        self.assertIn("community", caught.exception.message_dict)

    def test_accepting_a_reference_never_puts_anybody_in_a_circle(self):
        application = self._application(self.seniors)      # written past the form
        ref = ReferenceRequest.objects.create(application=application, referrer=self.senior)
        ref.status = "accepted"
        with self.assertRaises(ValidationError):
            ref.full_clean()
        with self.assertRaises(ValidationError):
            ref.save()
        ref.refresh_from_db()
        self.assertEqual(ref.status, "pending")
        applicant = User.objects.get(email="applicant@example.com")
        self.assertFalse(applicant.is_active)
        self.assertFalse(self.seniors.members.filter(user=applicant).exists())

    def test_the_accept_button_refuses_too(self):
        application = self._application(self.seniors)
        ref = ReferenceRequest.objects.create(application=application, referrer=self.senior)
        self.client.force_login(self.senior.user)
        response = self.client.post(reverse("socialhub:reference_accept", args=[ref.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.seniors.members.filter(user__email="applicant@example.com").exists())

    def test_a_functional_community_still_admits(self):
        application = self._application(self.devs)
        ref = ReferenceRequest.objects.create(application=application, referrer=self.senior)
        ref.status = "accepted"
        ref.save()
        self.assertTrue(self.devs.members.filter(user__email="applicant@example.com").exists())
