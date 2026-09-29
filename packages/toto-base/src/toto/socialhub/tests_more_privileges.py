"""What a community grants, and the doors that ask (``privileges``,
``permissions``, the chain graph and Administrata).

Rights are held through functional communities only: a circle grants
nothing, and every failure degrades to the commoner. Run in the host-owned
block beside ``tests_circles``:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_privileges
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import DatabaseError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import permissions, privileges
from toto.socialhub.models import Community, CommunityPrivilege

User = get_user_model()


def person(username, *communities, **flags):
    user = User.objects.create_user(username, f"{username}@example.com", "pw", **flags)
    someone = Person.objects.create(user=user, display_name=username.title())
    someone.communities.add(*communities)
    return someone


class PrivilegeCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.agents = Community.objects.create(name="agents", slug="agents")
        CommunityPrivilege.objects.create(community=cls.agents, may_see_community_chain=True,
                                          may_administer_communities=True,
                                          may_manage_community_news=True)
        cls.weavers = Community.objects.create(name="weavers", slug="weavers")
        cls.board = Community.objects.create(name="board", slug="board", is_circle=True)
        cls.agent = person("agent", cls.agents)
        cls.weaver = person("weaver", cls.weavers)


class ResolverTests(PrivilegeCase):
    def test_a_login_with_no_person_holds_nothing(self):
        stray = User.objects.create_user("stray", password="pw")
        for right in privileges.RIGHTS:
            self.assertFalse(privileges.has_privilege(stray, right))

    def test_a_grant_is_one_right_not_all_of_them(self):
        self.assertTrue(privileges.has_privilege(self.agent.user, "may_manage_community_news"))
        self.assertFalse(privileges.has_privilege(self.agent.user, "may_operate_mint"))

    def test_a_database_fault_answers_no_rather_than_raising(self):
        with mock.patch("toto.socialhub.models.CommunityQuerySet.functional",
                        side_effect=DatabaseError("mid-migrate")):
            self.assertFalse(privileges.has_privilege(self.agent.user, "may_see_community_chain"))

    def test_a_fault_while_finding_the_person_answers_no(self):
        stray = User.objects.create_user("stray", password="pw")
        with mock.patch.object(Person.objects, "filter", side_effect=DatabaseError("gone")):
            self.assertFalse(privileges.has_privilege(stray, "may_see_community_chain"))

    def test_nobody_signed_in_holds_anything(self):
        for user in (None, AnonymousUser()):
            self.assertFalse(privileges.has_privilege(user, "may_operate_mint"))

    def test_a_mistyped_right_raises_even_for_anonymous(self):
        with self.assertRaises(ValueError):
            privileges.has_privilege(AnonymousUser(), "may_see_everything")

    def test_being_in_a_circle_as_well_changes_nothing(self):
        self.weaver.communities.add(self.board)
        self.assertFalse(privileges.has_privilege(self.weaver.user, "may_see_community_chain"))
        self.agent.communities.add(self.board)
        self.assertTrue(privileges.has_privilege(self.agent.user, "may_see_community_chain"))


class NewsPermissionTests(PrivilegeCase):
    def can(self, user, community):
        request = RequestFactory().get("/")
        request.user = user
        return permissions.can_manage_community_news(request, community)

    def test_the_head_and_the_seniors_manage_their_own_community_only(self):
        head = person("head", self.weavers)
        senior = person("senior", self.weavers)
        self.weavers.head = head
        self.weavers.save()
        self.weavers.senior_members.add(senior)
        self.assertTrue(self.can(head.user, self.weavers))
        self.assertTrue(self.can(senior.user, self.weavers))
        self.assertFalse(self.can(head.user, self.agents))
        self.assertFalse(self.can(senior.user, self.agents))

    def test_a_plain_member_does_not(self):
        self.assertFalse(self.can(self.weaver.user, self.weavers))

    def test_a_granting_community_reaches_across_every_community(self):
        self.assertTrue(self.can(self.agent.user, self.weavers))

    def test_staff_and_superusers_do_and_anonymous_and_personless_do_not(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        root = User.objects.create_superuser("root", "root@example.com", "pw")
        stray = User.objects.create_user("stray", password="pw")
        self.assertTrue(self.can(staff, self.weavers))
        self.assertTrue(self.can(root, self.weavers))
        self.assertFalse(self.can(AnonymousUser(), self.weavers))
        self.assertFalse(self.can(stray, self.weavers))

    def test_current_person(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        self.assertIsNone(permissions.current_person(request))
        request.user = User.objects.create_user("stray", password="pw")
        self.assertIsNone(permissions.current_person(request))
        request.user = self.weaver.user
        self.assertEqual(permissions.current_person(request), self.weaver)


class ChainAndAdministrataTests(PrivilegeCase):
    def test_the_chain_is_refused_without_the_right(self):
        self.client.force_login(self.weaver.user)
        self.assertEqual(self.client.get(
            reverse("socialhub:community_chain_graph_data", args=["weavers"])).status_code, 403)
        self.assertEqual(self.client.get(
            reverse("socialhub:administrata", args=["weavers"])).status_code, 403)

    def test_the_chain_draws_parents_and_heads_it_may_show(self):
        child = Community.objects.create(name="weavers-north", slug="weavers-north",
                                         parent=self.weavers, head=self.weaver)
        self.client.force_login(self.agent.user)
        graph = self.client.get(reverse("socialhub:community_chain_graph_data",
                                        args=["weavers"])).json()
        ids = {node["id"] for node in graph["nodes"]}
        self.assertIn(f"p-{self.weaver.pk}", ids)
        self.assertNotIn(f"c-{self.board.pk}", ids)
        edges = {(e["source"], e["target"], e["type"]) for e in graph["edges"]}
        self.assertIn((f"c-{self.weavers.pk}", f"c-{child.pk}", "parent_child"), edges)
        self.assertIn((f"c-{child.pk}", f"p-{self.weaver.pk}", "head"), edges)

    def test_a_head_of_two_communities_is_drawn_once(self):
        self.weavers.head = self.weaver
        self.weavers.save()
        Community.objects.create(name="spinners", slug="spinners", head=self.weaver)
        self.client.force_login(self.agent.user)
        graph = self.client.get(reverse("socialhub:community_chain_graph_data",
                                        args=["weavers"])).json()
        people = [n for n in graph["nodes"] if n["type"] == "person"]
        self.assertEqual(len(people), 1)
        self.assertEqual(sum(1 for e in graph["edges"] if e["type"] == "head"), 2)

    def test_administrata_opens_for_the_right_and_links_its_graph(self):
        self.client.force_login(self.agent.user)
        response = self.client.get(reverse("socialhub:administrata", args=["weavers"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["graph_data_url"],
                         reverse("socialhub:community_chain_graph_data", args=["weavers"]))

    def test_the_community_page_shows_the_button_to_the_right_only(self):
        self.client.force_login(self.agent.user)
        self.assertTrue(self.client.get(reverse("socialhub:community_detail", args=["weavers"]))
                        .context["viewer_is_federal_agent"])
        self.client.force_login(self.weaver.user)
        self.assertFalse(self.client.get(reverse("socialhub:community_detail", args=["weavers"]))
                         .context["viewer_is_federal_agent"])

    def test_the_org_chart_follows_patrons(self):
        self.weaver.patron = self.agent
        self.weaver.save()
        self.agent.communities.add(self.weavers)
        self.client.force_login(self.weaver.user)
        nodes = self.client.get(reverse("socialhub:community_org_chart_data_by_slug",
                                        args=["weavers"])).json()["nodes"]
        by_name = {n["name"]: n for n in nodes}
        self.assertEqual(set(by_name), {"Weaver", "Agent"})
        self.assertEqual(by_name["Weaver"]["pid"], str(self.agent.pk))
        self.assertIsNone(by_name["Agent"]["pid"])
