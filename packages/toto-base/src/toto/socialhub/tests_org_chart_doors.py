"""The doors, the tab and the drawing of a community's organisation chart
(2026-10-06, stage 66): the head and administrators change it, checked on
the server at every door, with the community and the position taken from
the address; every change is on the audit chain.

    manage.py test toto.socialhub.tests_org_chart_doors
"""

import io

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import org_chart
from toto.socialhub.models import Community, CommunityPosition

User = get_user_model()

SIGNED_OUT = (302, 403)
SECTION = 'data-testid="community-org-chart"'
FORM = 'data-testid="org-chart-form"'


def member(name, display=None, **flags):
    user = User.objects.create_user(name, password="pw", **flags)
    return user, Person.objects.create(user=user, display_name=display or name.title(),
                                       slug=name)


def client_of(user):
    client = Client()
    client.force_login(user)
    return client


class DoorsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "A", "publication_year": 2026})
        cls.head_user, cls.head = member("hugo")
        cls.other_head_user, cls.other_head = member("olga")
        cls.mia_user, cls.mia = member("mia")
        cls.sen_user, cls.sen = member("sen")
        cls.stan_user, cls.stan = member("stan")
        cls.stef_user, cls.stef = member("stef", is_staff=True)
        bare, cls.root = member("root", is_superuser=True, is_staff=True)
        cls.guild = Community.objects.create(name="Guild", head=cls.head)
        cls.firm = Community.objects.create(name="Firm", org_type="company",
                                            head=cls.other_head)
        cls.mia.communities.add(cls.guild)
        cls.guild.senior_members.add(cls.sen)
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
        cls.root_user = User.objects.get(pk=bare.pk)
        cls.late_user, cls.late = member("late", is_superuser=True, is_staff=True)

    def setUp(self):
        self.chief = org_chart.create(self.guild, title="Chief", person=self.head)
        self.lead = org_chart.create(self.guild, title="Lead", reports_to=self.chief.pk)
        self.director = org_chart.create(self.firm, title="Director", person=self.other_head)

    @staticmethod
    def create_url(community):
        return reverse("socialhub:position_create", args=[community.slug])

    @staticmethod
    def edit_url(community, pk):
        return reverse("socialhub:position_edit", args=[community.slug, pk])

    @staticmethod
    def delete_url(community, pk):
        return reverse("socialhub:position_delete", args=[community.slug, pk])

    @staticmethod
    def page(community, tab="chart"):
        url = reverse("socialhub:community_detail", args=[community.slug])
        return f"{url}?tab={tab}" if tab else url

    def state(self):
        return sorted(CommunityPosition.objects.values_list(
            "community__slug", "title", "person__slug", "reports_to__title", "order"))

    def doors(self, community=None, pk=None):
        community = community or self.guild
        pk = pk or self.lead.pk
        return [("create", self.create_url(community), {"title": "Clerk"}),
                ("edit", self.edit_url(community, pk),
                 {"title": "Renamed", "person": "mia", "reports_to": ""}),
                ("delete", self.delete_url(community, pk), {})]


class PermissionTests(DoorsTestCase):
    def assert_refused(self, user, status, community=None, pk=None):
        before = self.state()
        client = client_of(user) if user is not None else Client()
        for name, url, body in self.doors(community, pk):
            with self.subTest(door=name, user=getattr(user, "username", None)):
                response = client.post(url, body)
                if isinstance(status, tuple):
                    self.assertIn(response.status_code, status)
                else:
                    self.assertEqual(response.status_code, status)
                self.assertEqual(self.state(), before)

    def test_a_visitor_is_refused_at_every_door(self):
        self.assert_refused(None, SIGNED_OUT)

    def test_a_member_is_refused_at_every_door(self):
        self.assert_refused(self.mia_user, 403)

    def test_a_senior_member_is_refused_at_every_door(self):
        self.assert_refused(self.sen_user, 403)

    def test_a_stranger_is_refused_at_every_door(self):
        self.assert_refused(self.stan_user, 403)

    def test_staff_alone_is_refused_at_every_door(self):
        self.assert_refused(self.stef_user, 403)

    def test_another_communitys_head_is_refused_at_every_door(self):
        self.assert_refused(self.other_head_user, 403)

    def test_a_superuser_without_the_plan_is_refused_at_every_door(self):
        from toto.socialhub.contact_access import is_administrator

        if is_administrator(self.late_user):
            self.skipTest("this host sells no Superuser plan: the privilege alone decides")
        self.assert_refused(self.late_user, 403)

    def test_the_person_assigned_to_a_position_gets_nothing_by_it(self):
        org_chart.change(self.guild, self.chief.pk, title="Chief", person=self.stan)
        self.assert_refused(self.stan_user, 403)

    def test_the_head_passes_every_door(self):
        client = client_of(self.head_user)
        for name, url, body in self.doors():
            with self.subTest(door=name):
                response = client.post(url, body)
                self.assertEqual(response.status_code, 302)
                self.assertIn("tab=chart", response["Location"])
        self.assertEqual(sorted(CommunityPosition.objects.filter(community=self.guild)
                                .values_list("title", flat=True)), ["Chief", "Clerk"])

    def test_an_administrator_passes_every_door_of_any_community(self):
        client = client_of(self.root_user)
        for community, pk in ((self.guild, self.lead.pk), (self.firm, self.director.pk)):
            for name, url, body in self.doors(community, pk):
                with self.subTest(door=name, community=community.slug):
                    self.assertEqual(client.post(url, body).status_code, 302)

    def test_every_door_takes_post_only(self):
        before = self.state()
        client = client_of(self.head_user)
        for name, url, body in self.doors():
            for method in ("get", "put", "delete", "patch"):
                with self.subTest(door=name, method=method):
                    response = getattr(client, method)(url)
                    self.assertEqual(response.status_code, 405)
                    self.assertEqual(response["Allow"], "POST")
        self.assertEqual(self.state(), before)

    def test_an_unknown_community_is_not_found(self):
        client = client_of(self.root_user)
        for url in ("/socialhub/communities/no-such/positions/",
                    f"/socialhub/communities/no-such/positions/{self.lead.pk}/",
                    f"/socialhub/communities/no-such/positions/{self.lead.pk}/delete/"):
            self.assertEqual(client.post(url, {"title": "X"}).status_code, 404)

    def test_another_communitys_position_is_not_found_under_this_address(self):
        before = self.state()
        for user in (self.head_user, self.root_user):
            client = client_of(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.post(self.edit_url(self.guild, self.director.pk),
                                             {"title": "Taken"}).status_code, 404)
                self.assertEqual(client.post(self.delete_url(self.guild, self.director.pk))
                                 .status_code, 404)
        self.assertEqual(self.state(), before)

    def test_a_position_that_is_not_there_is_not_found(self):
        client = client_of(self.head_user)
        self.assertEqual(client.post(self.edit_url(self.guild, 999999),
                                     {"title": "X"}).status_code, 404)
        self.assertEqual(client.post(self.delete_url(self.guild, 999999)).status_code, 404)

    def test_every_door_carries_the_mark(self):
        from toto.socialhub import urls

        doors = [pattern for pattern in urls.urlpatterns
                 if (pattern.name or "").startswith("position_")]
        self.assertEqual(len(doors), 3)
        for pattern in doors:
            self.assertTrue(getattr(pattern.callback, "org_chart_door", False), pattern.name)


class ChangeTests(DoorsTestCase):
    def post(self, url, body, user=None):
        return client_of(user or self.head_user).post(url, body, follow=True)

    def test_a_position_is_made_with_person_superior_and_no_order(self):
        self.post(self.create_url(self.guild),
                  {"title": "Clerk", "person": "mia", "reports_to": str(self.lead.pk)})
        clerk = CommunityPosition.objects.get(title="Clerk")
        self.assertEqual((clerk.community, clerk.person, clerk.reports_to, clerk.order),
                         (self.guild, self.mia, self.lead, 0))

    def test_a_ring_through_the_door_is_refused_and_said(self):
        response = self.post(self.edit_url(self.guild, self.chief.pk),
                             {"title": "Chief", "person": "hugo",
                              "reports_to": str(self.lead.pk)})
        self.assertContains(response, "cannot report to itself or to one that reports to it")
        self.assertIsNone(CommunityPosition.objects.get(pk=self.chief.pk).reports_to)

    def test_itself_as_its_superior_is_refused(self):
        self.post(self.edit_url(self.guild, self.lead.pk),
                  {"title": "Lead", "reports_to": str(self.lead.pk)})
        self.assertEqual(CommunityPosition.objects.get(pk=self.lead.pk).reports_to, self.chief)

    def test_another_communitys_position_as_superior_is_refused(self):
        before = self.state()
        self.post(self.create_url(self.guild),
                  {"title": "Clerk", "reports_to": str(self.director.pk)})
        self.post(self.edit_url(self.guild, self.lead.pk),
                  {"title": "Lead", "reports_to": str(self.director.pk)})
        self.assertEqual(self.state(), before)

    def test_an_unknown_person_is_refused(self):
        before = self.state()
        self.post(self.create_url(self.guild), {"title": "Clerk", "person": "nobody-here"})
        self.assertEqual(self.state(), before)

    def test_a_name_that_is_none_is_refused(self):
        before = self.state()
        for title in ("", "   ", "x" * 121):
            self.post(self.create_url(self.guild), {"title": title})
        self.assertEqual(self.state(), before)

    def test_an_edit_can_vacate_and_lift_to_the_top(self):
        self.post(self.edit_url(self.guild, self.lead.pk),
                  {"title": "Lead", "person": "", "reports_to": "", "order": "3"})
        lead = CommunityPosition.objects.get(pk=self.lead.pk)
        self.assertEqual((lead.person, lead.reports_to, lead.order), (None, None, 3))

    def test_deleting_through_the_door_re_hangs_the_reports(self):
        clerk = org_chart.create(self.guild, title="Clerk", reports_to=self.lead.pk)
        self.post(self.delete_url(self.guild, self.lead.pk), {})
        self.assertEqual(CommunityPosition.objects.get(pk=clerk.pk).reports_to, self.chief)


class PageTests(DoorsTestCase):
    def tabs(self, user, community):
        response = client_of(user).get(self.page(community, ""))
        return [tab["key"] for tab in response.context["community_tabs"]]

    def test_a_community_with_positions_shows_the_tab_to_signed_in_viewers(self):
        for user in (self.mia_user, self.stan_user, self.head_user, self.stef_user):
            with self.subTest(user=user.username):
                self.assertIn("chart", self.tabs(user, self.guild))

    def test_an_empty_chart_is_a_tab_for_who_may_change_it_alone(self):
        empty = Community.objects.create(name="Empty", head=self.head)
        self.assertIn("chart", self.tabs(self.head_user, empty))
        self.assertIn("chart", self.tabs(self.root_user, empty))
        for user in (self.mia_user, self.stan_user, self.stef_user):
            with self.subTest(user=user.username):
                self.assertNotIn("chart", self.tabs(user, empty))
                response = client_of(user).get(self.page(empty))
                self.assertNotContains(response, SECTION)
                self.assertNotContains(response, 'data-testid="community-tabs"')

    def test_the_tab_is_a_table_of_position_person_and_superior(self):
        org_chart.create(self.guild, title="Clerk", reports_to=self.lead.pk)
        response = client_of(self.mia_user).get(self.page(self.guild))
        self.assertContains(response, SECTION)
        text = response.content.decode()
        table = text[text.index('data-testid="org-chart-table"'):]
        table = table[:table.index("</table>")]
        for expected in ("Position", "Person", "Reports to", "Chief", "Lead", "Clerk",
                         "Hugo", "Vacant", "/socialhub/profiles/hugo/"):
            self.assertIn(expected, table)
        self.assertLess(table.index("Chief"), table.index("Lead"))
        self.assertLess(table.index("Lead"), table.index("Clerk"))
        self.assertLess(text.index('<div class="overflow-x-auto">'),
                        text.index('data-testid="org-chart-table"'))

    def test_a_company_has_the_same_tab(self):
        response = client_of(self.mia_user).get(self.page(self.firm))
        self.assertContains(response, SECTION)
        self.assertContains(response, "Director")

    def test_who_may_not_change_it_gets_no_form(self):
        for user in (self.mia_user, self.sen_user, self.stan_user, self.stef_user,
                     self.other_head_user):
            with self.subTest(user=user.username):
                response = client_of(user).get(self.page(self.guild))
                self.assertContains(response, SECTION)
                self.assertNotContains(response, FORM)
                self.assertNotContains(response, self.create_url(self.guild))
                self.assertNotContains(response, self.delete_url(self.guild, self.lead.pk))
                self.assertNotContains(response, 'name="reports_to"')

    def test_the_head_and_an_administrator_get_the_forms(self):
        for user in (self.head_user, self.root_user):
            with self.subTest(user=user.username):
                response = client_of(user).get(self.page(self.guild))
                self.assertContains(response, FORM)
                self.assertContains(response, self.create_url(self.guild))
                self.assertContains(response, self.edit_url(self.guild, self.lead.pk))
                self.assertContains(response, self.delete_url(self.guild, self.lead.pk))
                self.assertContains(response, "csrfmiddlewaretoken")

    def test_names_reach_the_page_as_text(self):
        _user, evil = member("evil", display="<script>alert(1)</script>")
        org_chart.create(self.guild, title="<b>Boss</b>", person=evil,
                         reports_to=self.chief.pk)
        for user in (self.mia_user, self.head_user):
            text = client_of(user).get(self.page(self.guild)).content.decode()
            self.assertNotIn("<b>Boss</b>", text)
            self.assertNotIn("<script>alert(1)</script>", text)
            self.assertIn("&lt;b&gt;Boss&lt;/b&gt;", text)

    def test_a_visitor_gets_no_chart(self):
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        from toto.socialhub.plugins.community_plugins import CommunityPlugin

        response = Client().get(self.page(self.guild))
        if response.status_code not in SIGNED_OUT:
            self.assertNotContains(response, SECTION)
        request = RequestFactory().get(self.page(self.guild))
        request.user = AnonymousUser()
        plugin = CommunityPlugin.get("community_org_chart")
        self.assertFalse(plugin.is_visible(request=request, community=self.guild))


class DrawingTests(DoorsTestCase):
    def data(self, user, community):
        url = reverse("socialhub:community_org_chart_data_by_slug", args=[community.slug])
        return client_of(user).get(url).json()

    def test_the_org_chart_button_draws_the_positions(self):
        data = self.data(self.mia_user, self.guild)
        self.assertEqual(data["kind"], "positions")
        nodes = {node["title"]: node for node in data["nodes"]}
        self.assertEqual(set(nodes), {"Chief", "Lead"})
        self.assertEqual(nodes["Chief"]["name"], "Hugo")
        self.assertEqual(nodes["Lead"]["name"], "Vacant")
        self.assertEqual(nodes["Lead"]["pid"], nodes["Chief"]["id"])
        for node in data["nodes"]:
            self.assertEqual(set(node), {"id", "name", "title", "pid", "profile_url"})

    def test_a_community_without_positions_draws_its_members_as_before(self):
        plain = Community.objects.create(name="Plain")
        self.mia.communities.add(plain)
        self.stan.patron = self.mia
        self.stan.save()
        self.stan.communities.add(plain)
        data = self.data(self.mia_user, plain)
        self.assertEqual(data["kind"], "patrons")
        nodes = {node["name"]: node for node in data["nodes"]}
        self.assertEqual(set(nodes), {"Mia", "Stan"})
        self.assertEqual(nodes["Stan"]["pid"], str(self.mia.pk))
        self.assertEqual(nodes["Mia"]["title"], "")

    def test_the_page_still_has_the_button_and_the_drawing(self):
        response = client_of(self.mia_user).get(self.page(self.guild, ""))
        self.assertContains(response, "Org Chart")
        self.assertContains(response, 'id="orgchart-container"')
        self.assertContains(response, reverse("socialhub:community_org_chart_data_by_slug",
                                              args=[self.guild.slug]))


class AuditTests(DoorsTestCase):
    def setUp(self):
        if not apps.is_installed("toto.audit"):
            self.skipTest("no audit chain on this host")
        super().setUp()
        from toto.audit.models import AuditRecord

        self.AuditRecord = AuditRecord
        self.start = (AuditRecord.objects.order_by("-sequence")
                      .values_list("sequence", flat=True).first() or 0)

    def records(self):
        return list(self.AuditRecord.objects.filter(action__startswith="SOCIALHUB.POSITION_",
                                                    sequence__gt=self.start)
                    .order_by("sequence"))

    def test_made_changed_and_removed_are_recorded_with_who_did_it(self):
        client = client_of(self.head_user)
        client.post(self.create_url(self.guild),
                    {"title": "Clerk", "person": "mia", "reports_to": str(self.lead.pk)})
        clerk = CommunityPosition.objects.get(title="Clerk")
        client.post(self.edit_url(self.guild, clerk.pk),
                    {"title": "Scribe", "person": "", "reports_to": str(self.chief.pk)})
        client.post(self.delete_url(self.guild, clerk.pk))
        made, changed, removed = self.records()
        self.assertEqual([made.action, changed.action, removed.action],
                         ["SOCIALHUB.POSITION_CREATED", "SOCIALHUB.POSITION_CHANGED",
                          "SOCIALHUB.POSITION_DELETED"])
        self.assertEqual(made.metadata, {"community": "guild", "position": clerk.pk,
                                         "title": "Clerk", "person": "mia",
                                         "reports_to": self.lead.pk, "order": 0})
        self.assertEqual(changed.metadata["changed"], ["person", "reports_to", "title"])
        self.assertEqual(changed.metadata["before"],
                         {"title": "Clerk", "person": "mia", "reports_to": self.lead.pk})
        self.assertEqual(changed.metadata["after"],
                         {"title": "Scribe", "person": None, "reports_to": self.chief.pk})
        self.assertEqual(removed.metadata["title"], "Scribe")
        for record in (made, changed, removed):
            self.assertEqual(record.actor_user, self.head_user)
            self.assertEqual((record.app_label, record.object_type, record.object_id),
                             ("socialhub", "socialhub.communityposition", str(clerk.pk)))

    def test_a_save_that_changes_nothing_records_nothing(self):
        client_of(self.head_user).post(
            self.edit_url(self.guild, self.lead.pk),
            {"title": "Lead", "person": "", "reports_to": str(self.chief.pk), "order": "0"})
        self.assertEqual(self.records(), [])

    def test_a_refusal_records_nothing(self):
        client_of(self.mia_user).post(self.create_url(self.guild), {"title": "Clerk"})
        client_of(self.head_user).post(self.edit_url(self.guild, self.chief.pk),
                                       {"title": "Chief", "reports_to": str(self.lead.pk)})
        client_of(self.head_user).post(self.delete_url(self.guild, self.director.pk))
        self.assertEqual(self.records(), [])

    def test_a_delete_records_the_re_hanging_of_each_report(self):
        clerk = org_chart.create(self.guild, title="Clerk", reports_to=self.lead.pk)
        self.start = (self.AuditRecord.objects.order_by("-sequence")
                      .values_list("sequence", flat=True).first() or 0)
        client_of(self.root_user).post(self.delete_url(self.guild, self.lead.pk))
        moved, removed = self.records()
        self.assertEqual((moved.action, removed.action),
                         ("SOCIALHUB.POSITION_CHANGED", "SOCIALHUB.POSITION_DELETED"))
        self.assertEqual(moved.object_id, str(clerk.pk))
        self.assertEqual(moved.metadata["before"], {"reports_to": self.lead.pk})
        self.assertEqual(moved.metadata["after"], {"reports_to": self.chief.pk})
        self.assertEqual(moved.actor_user, self.root_user)
