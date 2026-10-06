"""A community's organisation chart (2026-10-06, stage 66): positions, the
people assigned, who reports to whom, and no ring among them.

    manage.py test toto.socialhub.tests_org_chart
"""

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from toto.people.models import Person
from toto.socialhub import org_chart
from toto.socialhub.models import Community, CommunityPosition
from toto.socialhub.org_chart import ChartError

User = get_user_model()


def person(name):
    user = User.objects.create_user(name, password="pw")
    return Person.objects.create(user=user, display_name=name.title(), slug=name)


class ChartTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.ada, cls.bob, cls.cy = person("ada"), person("bob"), person("cy")
        cls.guild = Community.objects.create(name="Guild", head=cls.ada)
        cls.firm = Community.objects.create(name="Firm", org_type="company", head=cls.bob)

    def make(self, title, reports_to=None, person=None, community=None, order=0):
        return org_chart.create(community or self.guild, title=title, person=person,
                                reports_to=reports_to.pk if reports_to else None, order=order)

    def move(self, position, reports_to, community=None):
        return org_chart.change(community or self.guild, position.pk, title=position.title,
                                person=position.person,
                                reports_to=reports_to.pk if reports_to else None,
                                order=position.order)

    def superior_of(self, position):
        return CommunityPosition.objects.get(pk=position.pk).reports_to_id


class CycleTests(ChartTestCase):
    def setUp(self):
        self.chief = self.make("Chief")
        self.lead = self.make("Lead", self.chief)
        self.clerk = self.make("Clerk", self.lead)

    def test_a_ring_of_one_is_refused(self):
        with self.assertRaises(ChartError):
            self.move(self.chief, self.chief)
        self.assertIsNone(self.superior_of(self.chief))

    def test_a_ring_of_two_is_refused(self):
        with self.assertRaises(ChartError):
            self.move(self.chief, self.lead)
        self.assertIsNone(self.superior_of(self.chief))
        self.assertEqual(self.superior_of(self.lead), self.chief.pk)

    def test_a_ring_of_three_is_refused(self):
        with self.assertRaises(ChartError):
            self.move(self.chief, self.clerk)
        self.assertIsNone(self.superior_of(self.chief))

    def test_a_long_ring_is_refused(self):
        last = self.clerk
        for number in range(12):
            last = self.make(f"Deep {number}", last)
        with self.assertRaises(ChartError):
            self.move(self.chief, last)
        with self.assertRaises(ChartError):
            self.move(self.lead, last)
        self.assertIsNone(self.superior_of(self.chief))

    def test_a_refused_move_changes_nothing_else_either(self):
        with self.assertRaises(ChartError):
            org_chart.change(self.guild, self.chief.pk, title="Renamed", person=self.bob,
                             reports_to=self.clerk.pk, order=7)
        chief = CommunityPosition.objects.get(pk=self.chief.pk)
        self.assertEqual((chief.title, chief.person, chief.order), ("Chief", None, 0))

    def test_a_legal_move_is_made(self):
        aide = self.make("Aide", self.chief)
        self.move(self.clerk, aide)
        self.assertEqual(self.superior_of(self.clerk), aide.pk)
        self.move(self.clerk, None)
        self.assertIsNone(self.superior_of(self.clerk))

    def test_moving_a_branch_under_a_sibling_branch_is_legal(self):
        other = self.make("Other lead", self.chief)
        self.move(self.lead, other)
        self.assertEqual(self.superior_of(self.lead), other.pk)
        self.assertEqual(self.superior_of(self.clerk), self.lead.pk)
        with self.assertRaises(ChartError):          # and now the other way is a ring
            self.move(other, self.clerk)

    def test_after_any_number_of_moves_there_is_still_no_ring(self):
        extra = [self.make(f"Extra {number}") for number in range(4)]
        everyone = [self.chief, self.lead, self.clerk, *extra]
        for mover in everyone:
            for target in everyone:
                try:
                    self.move(mover, target)
                except ChartError:
                    pass
        for position in CommunityPosition.objects.filter(community=self.guild):
            seen, current = set(), position
            while current is not None:
                self.assertNotIn(current.pk, seen, "a ring in the chart")
                seen.add(current.pk)
                current = current.reports_to

    def test_the_database_refuses_a_position_reporting_to_itself(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            CommunityPosition.objects.filter(pk=self.chief.pk).update(reports_to=self.chief.pk)

    def test_would_ring_stops_on_a_ring_already_in_the_table(self):
        """Written past the rules, straight into the table: the walk ends."""
        CommunityPosition.objects.filter(pk=self.chief.pk).update(reports_to=self.clerk.pk)
        loose = self.make("Loose")
        lead = CommunityPosition.objects.get(pk=self.lead.pk)
        self.assertFalse(org_chart.would_ring(loose.pk, lead))
        self.assertTrue(org_chart.would_ring(self.lead.pk, lead))
        rows = org_chart.chart_of(self.guild)                 # and the page still draws
        self.assertEqual(len(rows), 4)
        self.assertEqual(len(org_chart.nodes_of(self.guild)), 4)

    def test_every_change_locks_the_communitys_row_first(self):
        from unittest import mock

        with mock.patch.object(org_chart, "_lock", wraps=org_chart._lock) as lock:
            made = self.make("New")
            self.move(made, self.chief)
            org_chart.delete(self.guild, made.pk)
        self.assertEqual(lock.call_count, 3)
        for call in lock.call_args_list:
            self.assertEqual(call.args[0], self.guild)


class CommunityTests(ChartTestCase):
    def test_a_position_reports_only_within_its_community(self):
        foreign = self.make("Director", community=self.firm)
        with self.assertRaises(ChartError):
            self.make("Clerk", foreign)
        clerk = self.make("Clerk")
        with self.assertRaises(ChartError):
            self.move(clerk, foreign)
        self.assertIsNone(self.superior_of(clerk))
        self.assertEqual(CommunityPosition.objects.filter(community=self.guild).count(), 1)

    def test_a_position_is_changed_only_through_its_own_community(self):
        director = self.make("Director", community=self.firm)
        self.assertIsNone(org_chart.change(self.guild, director.pk, title="Taken",
                                           person=None, reports_to=None, order=0))
        self.assertFalse(org_chart.delete(self.guild, director.pk))
        self.assertEqual(CommunityPosition.objects.get(pk=director.pk).title, "Director")

    def test_a_superior_that_is_not_there_is_refused(self):
        for wanted in (999999, "abc", "-1", "1.5"):
            with self.subTest(wanted=wanted), self.assertRaises(ChartError):
                org_chart.create(self.guild, title="Clerk", reports_to=wanted)
        self.assertFalse(CommunityPosition.objects.exists())

    def test_every_kind_of_community_has_a_chart_and_a_company_as_any(self):
        self.make("Warden")
        self.make("Director", community=self.firm)
        self.assertEqual([row.position.title for row in org_chart.chart_of(self.guild)],
                         ["Warden"])
        self.assertEqual([row.position.title for row in org_chart.chart_of(self.firm)],
                         ["Director"])

    def test_the_chart_goes_with_its_community(self):
        self.make("Director", community=self.firm)
        self.firm.delete()
        self.assertFalse(CommunityPosition.objects.exists())


class PositionTests(ChartTestCase):
    def test_a_position_has_a_person_or_is_vacant(self):
        chief = self.make("Chief", person=self.ada)
        vacant = self.make("Deputy", chief)
        self.assertEqual(chief.person, self.ada)
        self.assertIsNone(vacant.person)

    def test_one_person_may_hold_two_positions(self):
        self.make("Chief", person=self.ada)
        self.make("Treasurer", person=self.ada)
        self.assertEqual(self.ada.community_positions.count(), 2)

    def test_the_person_need_not_be_a_member(self):
        self.assertFalse(self.cy.communities.filter(pk=self.guild.pk).exists())
        self.assertEqual(self.make("Auditor", person=self.cy).person, self.cy)
        self.assertFalse(self.cy.communities.filter(pk=self.guild.pk).exists())

    def test_an_erased_person_leaves_the_position_vacant(self):
        chief = self.make("Chief", person=self.cy)
        self.cy.user.delete()
        chief.refresh_from_db()
        self.assertIsNone(chief.person)
        self.assertEqual(chief.title, "Chief")

    def test_a_member_who_leaves_keeps_the_position_until_a_manager_changes_it(self):
        self.cy.communities.add(self.guild)
        chief = self.make("Chief", person=self.cy)
        self.cy.communities.remove(self.guild)
        chief.refresh_from_db()
        self.assertEqual(chief.person, self.cy)

    def test_the_name_is_cleaned_and_bounded(self):
        self.assertEqual(self.make("  Head   of  \t finance ").title, "Head of finance")
        for bad in ("", "   ", "x" * 121, "a\x00b", "a‮b"):
            with self.subTest(bad=repr(bad)), self.assertRaises(ChartError):
                self.make(bad)
        self.assertEqual(self.make("x" * 120).title, "x" * 120)

    def test_the_order_is_a_small_whole_number(self):
        self.assertEqual(self.make("A", order="12").order, 12)
        self.assertEqual(self.make("B", order="").order, 0)
        for bad in ("-1", "1.5", "ten", "10000", "１２"):
            with self.subTest(bad=bad), self.assertRaises(ChartError):
                self.make("C", order=bad)

    def test_deleting_hands_the_reports_to_the_one_above(self):
        chief = self.make("Chief")
        lead = self.make("Lead", chief)
        one, two = self.make("One", lead), self.make("Two", lead)
        self.assertTrue(org_chart.delete(self.guild, lead.pk))
        self.assertEqual(self.superior_of(one), chief.pk)
        self.assertEqual(self.superior_of(two), chief.pk)

    def test_deleting_a_top_position_makes_its_reports_top_positions(self):
        chief = self.make("Chief")
        lead = self.make("Lead", chief)
        org_chart.delete(self.guild, chief.pk)
        self.assertIsNone(self.superior_of(lead))
        self.assertEqual(CommunityPosition.objects.count(), 1)

    def test_a_position_that_is_not_there(self):
        self.assertFalse(org_chart.delete(self.guild, 999999))
        self.assertIsNone(org_chart.change(self.guild, 999999, title="X"))


class ReadingTests(ChartTestCase):
    def setUp(self):
        self.chief = self.make("Chief", person=self.ada)
        self.lead = self.make("Lead", self.chief, person=self.bob, order=2)
        self.aide = self.make("Aide", self.chief, order=1)
        self.clerk = self.make("Clerk", self.lead)

    def test_each_position_stands_under_what_it_reports_to(self):
        rows = org_chart.chart_of(self.guild)
        self.assertEqual([(row.position.title, row.depth) for row in rows],
                         [("Chief", 0), ("Aide", 1), ("Lead", 1), ("Clerk", 2)])

    def test_a_row_offers_no_superior_that_would_close_a_ring(self):
        offered = {row.position.title: {position.title for position in row.may_report_to}
                   for row in org_chart.chart_of(self.guild)}
        self.assertEqual(offered["Chief"], set())
        self.assertEqual(offered["Lead"], {"Chief", "Aide"})
        self.assertEqual(offered["Clerk"], {"Chief", "Aide", "Lead"})
        self.assertEqual(offered["Aide"], {"Chief", "Lead", "Clerk"})

    def test_the_nodes_for_the_drawing(self):
        nodes = {node["title"]: node for node in org_chart.nodes_of(self.guild)}
        self.assertEqual(set(nodes), {"Chief", "Lead", "Aide", "Clerk"})
        self.assertEqual(nodes["Chief"]["name"], "Ada")
        self.assertIsNone(nodes["Chief"]["pid"])
        self.assertEqual(nodes["Chief"]["profile_url"], "/socialhub/profiles/ada/")
        self.assertEqual(nodes["Lead"]["pid"], nodes["Chief"]["id"])
        self.assertEqual(nodes["Clerk"]["pid"], nodes["Lead"]["id"])
        self.assertEqual((nodes["Aide"]["name"], nodes["Aide"]["profile_url"]), ("Vacant", ""))
        self.assertEqual(len({node["id"] for node in nodes.values()}), 4)

    def test_every_line_of_the_drawing_ends_at_a_node_that_is_there(self):
        ids = {node["id"] for node in org_chart.nodes_of(self.guild)}
        for node in org_chart.nodes_of(self.guild):
            self.assertTrue(node["pid"] is None or node["pid"] in ids)

    def test_an_empty_chart(self):
        self.assertEqual(org_chart.chart_of(self.firm), [])
        self.assertEqual(org_chart.nodes_of(self.firm), [])


class PersonalDataTests(ChartTestCase):
    """"Download my data" holds the positions a member is assigned to, and
    nobody else's."""

    def tables(self, person):
        from toto.core.personal_data import tables_for

        return {table.name: table.rows for table in tables_for(person.user)}

    def test_a_members_copy_holds_their_positions(self):
        chief = self.make("Chief", person=self.bob)
        self.make("Lead", chief, person=self.ada)
        self.make("Director", person=self.ada, community=self.firm)
        self.make("Clerk", chief, person=self.cy)
        rows = self.tables(self.ada)["community_positions"]
        self.assertEqual([(row["community_slug"], row["position"], row["reports_to"])
                          for row in rows],
                         [("firm", "Director", ""), ("guild", "Lead", "Chief")])
        self.assertEqual(set(rows[0]), {"community", "community_slug", "position",
                                        "reports_to", "since", "changed"})

    def test_it_names_nobody_else(self):
        chief = self.make("Chief", person=self.bob)
        self.make("Lead", chief, person=self.ada)
        text = repr(self.tables(self.ada)["community_positions"])
        self.assertNotIn("Bob", text)
        self.assertNotIn("bob", text)

    def test_a_member_with_no_position_gets_an_empty_table(self):
        self.make("Chief", person=self.bob)
        self.assertEqual(self.tables(self.cy)["community_positions"], [])

