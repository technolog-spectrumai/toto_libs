"""The three views the brief names, their permissions, and the whole flow.

A scratch test DB has no Platform row, and `PageProcessor` needs one to
decorate a page — every view test here creates it first.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.company.models import Company, CompanyMembership, Department, ShareHolding
from toto.company.services.ownership import issue_shares
from toto.company.tests.factories import CompanyFactoryMixin
from toto.core.models import Platform
from toto.people.models import Person


class CompanyViewTestCase(CompanyFactoryMixin, TestCase):
    def setUp(self):
        Platform.objects.create(
            site_name="Test Platform", author="Tests",
            publication_year=2026, active=True,
        )
        self.staff = get_user_model().objects.create_user(
            "boss", password="x", is_staff=True,
        )
        self.member = get_user_model().objects.create_user("clerk", password="x")

        self.company = self.make_company()
        self.board = Department.objects.create(company=self.company, name="Board")
        self.finance = Department.objects.create(
            company=self.company, name="Finance", parent=self.board,
        )
        self.ada = self.make_party(self.company, "Ada")
        self.bob = self.make_party(self.company, "Bob")
        self.ordinary = self.make_share_class(self.company)
        self.preferred = self.make_share_class(
            self.company, name="Preferred", slug="preferred",
            votes_per_unit=Decimal("2"),
        )
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.ordinary, units=Decimal("60"))
        issue_shares(company=self.company, target_party=self.bob,
                     share_class=self.preferred, units=Decimal("20"))

        person = Person.objects.create(user=self.member, display_name="Clerk Person")
        CompanyMembership.objects.create(
            company=self.company, person=person,
            job_title="Bookkeeper", primary_department=self.finance,
        )


class AccessTests(CompanyViewTestCase):
    def test_every_page_requires_login(self):
        for name, args in (
            ("company:index", []),
            ("company:structure", [self.company.slug]),
            ("company:shareholders", [self.company.slug]),
            ("company:org_chart", [self.company.slug]),
            ("company:company_graph", [self.company.slug]),
        ):
            with self.subTest(name=name):
                response = self.client.get(reverse(name, args=args))
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])

    def test_a_plain_member_may_read_but_not_write(self):
        self.client.force_login(self.member)
        self.assertEqual(
            self.client.get(reverse("company:structure", args=[self.company.slug])).status_code, 200,
        )
        response = self.client.post(
            reverse("company:structure", args=[self.company.slug]),
            {"action": "company", "name": "Renamed"},
        )
        self.assertEqual(response.status_code, 403)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Farfarele Brokker Inc.")


class StructureViewTests(CompanyViewTestCase):
    def test_it_shows_members_departments_and_legal_facts(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:structure", args=[self.company.slug]))
        self.assertContains(response, "Clerk Person")
        self.assertContains(response, "Bookkeeper")
        self.assertContains(response, "Finance")
        self.assertContains(response, "Board")

    def test_the_department_table_lists_parents_before_children(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:structure", args=[self.company.slug]))
        rows = [d.name for d in response.context["departments"]]
        self.assertLess(rows.index("Board"), rows.index("Finance"))
        depths = {d.name: d.depth for d in response.context["departments"]}
        self.assertEqual(depths["Board"], 0)
        self.assertEqual(depths["Finance"], 1)

    def test_staff_can_add_a_department(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("company:structure", args=[self.company.slug]),
            {"action": "department", "name": "Legal", "active": "on"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Department.objects.filter(company=self.company, slug="legal").exists())

    def test_an_invalid_department_reopens_its_modal_instead_of_500ing(self):
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse("company:structure", args=[self.company.slug]),
            {"action": "department", "name": ""},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["open_modal"], "department")


class ShareholderViewTests(CompanyViewTestCase):
    def test_the_register_and_its_totals_are_exact(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:shareholders", args=[self.company.slug]))
        self.assertEqual(response.context["total_units"], Decimal("80.000000"))
        # Bob's 20 preferred carry 2 votes each: 60 + 40 = 100.
        self.assertEqual(response.context["total_votes"], Decimal("100.000000"))

    def test_there_are_two_charts_and_they_disagree(self):
        """The page's whole argument. Ada owns 60 of 80 units (75% of the
        capital) but 60 of 100 votes (60% of the control), because Bob's
        preferred shares carry two votes each. One chart could not say that,
        and the single chart this page used to have was labelled ownership
        while it drew votes."""
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:shareholders", args=[self.company.slug]))

        ownership = json.loads(response.context["ownership_chart_json"])
        voting = json.loads(response.context["voting_chart_json"])
        self.assertEqual(ownership["chart_type"], "doughnut")
        self.assertEqual(ownership["labels"], ["Ada", "Bob"])
        self.assertEqual(ownership["datasets"][0]["data"], [60.0, 20.0])
        self.assertEqual(voting["labels"], ["Ada", "Bob"])
        self.assertEqual(voting["datasets"][0]["data"], [60.0, 40.0])

    def test_the_percentages_add_up_for_both_metrics(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:shareholders", args=[self.company.slug]))
        rows = response.context["shareholder_structure"]
        self.assertEqual(sum(row["ownership_percent"] for row in rows), 100)
        self.assertEqual(sum(row["voting_percent"] for row in rows), 100)

    def test_an_empty_register_charts_nothing_rather_than_dividing_by_zero(self):
        empty = self.make_company("Empty sp. z o.o.")
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:shareholders", args=[empty.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["ownership_chart_json"], "")
        self.assertEqual(response.context["voting_chart_json"], "")
        self.assertEqual(response.context["total_votes"], 0)

    def test_staff_can_record_a_holding_and_it_lands_in_the_register(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse("company:shareholders", args=[self.company.slug]),
            {
                "action": "shareholder",
                "party_name": "Cleo",
                "share_class": self.ordinary.pk,
                "units": "12.5",
            },
            follow=True,
        )
        holding = ShareHolding.objects.get(party__name="Cleo", until__isnull=True)
        self.assertEqual(holding.units, Decimal("12.500000"))


class OrgChartViewTests(CompanyViewTestCase):
    def test_the_page_offers_a_readable_fallback_beside_the_diagram(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:org_chart", args=[self.company.slug]))
        self.assertContains(response, "organization-chart")
        self.assertContains(response, "The same hierarchy, as a list")
        self.assertContains(response, "Finance")

    def test_the_graph_json_carries_the_company_and_its_tree(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:company_graph", args=[self.company.slug]))
        graph = response.json()
        labels = {node["label"] for node in graph["nodes"]}
        self.assertIn(self.company.name, labels)
        self.assertIn("Board", labels)
        self.assertIn("Finance", labels)
        # Finance hangs off Board, and Board off the company.
        edges = {(edge["source"], edge["target"]) for edge in graph["edges"]}
        self.assertIn((f"department-{self.board.pk}", f"department-{self.finance.pk}"), edges)
        self.assertIn((f"company-{self.company.pk}", f"department-{self.board.pk}"), edges)

    def test_people_appear_only_when_asked_for(self):
        self.finance.head = self.ada
        self.finance.save()
        self.client.force_login(self.member)

        plain = self.client.get(
            reverse("company:company_graph", args=[self.company.slug])
        ).json()
        self.assertIn("Ada", {node["label"] for node in plain["nodes"]})

        types = {node["type"] for node in plain["nodes"]}
        self.assertEqual(types, {"company", "department", "person"})

    def test_a_department_graph_is_rooted_at_that_department(self):
        self.client.force_login(self.member)
        graph = self.client.get(reverse(
            "company:department_graph", args=[self.company.slug, self.finance.slug],
        )).json()
        labels = {node["label"] for node in graph["nodes"]}
        self.assertIn("Finance", labels)
        self.assertNotIn("Board", labels)
        self.assertNotIn(self.company.name, labels)


class DepartmentPageTests(CompanyViewTestCase):
    def test_it_shows_the_path_children_and_members(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse(
            "company:department_detail", args=[self.company.slug, self.board.slug],
        ))
        self.assertContains(response, "Board")
        self.assertContains(response, "Finance")
        self.assertEqual([d.name for d in response.context["children"]], ["Finance"])

    def test_staff_can_add_a_member_to_a_department(self):
        self.client.force_login(self.staff)
        self.client.post(
            reverse("company:membership_add", args=[self.company.slug, self.finance.slug]),
            {"party": self.ada.pk, "title": "Controller", "joined_on": "2026-01-01"},
            follow=True,
        )
        self.assertTrue(self.finance.memberships.filter(party=self.ada).exists())

    def test_a_plain_member_cannot(self):
        self.client.force_login(self.member)
        response = self.client.post(
            reverse("company:membership_add", args=[self.company.slug, self.finance.slug]),
            {"party": self.ada.pk, "joined_on": "2026-01-01"},
        )
        self.assertEqual(response.status_code, 403)


class IndexTests(CompanyViewTestCase):
    def test_it_links_each_company_to_all_three_views(self):
        self.client.force_login(self.member)
        response = self.client.get(reverse("company:index"))
        self.assertContains(response, self.company.name)
        for name in ("company:structure", "company:shareholders", "company:org_chart"):
            self.assertContains(response, reverse(name, args=[self.company.slug]))
