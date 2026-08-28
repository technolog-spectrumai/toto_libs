"""The shareholder charts: what they draw, and that the page can draw them.

This page shipped with a canvas that stayed empty and a console reading
`Uncaught ReferenceError: dim is not defined`. The cause was not the data —
the payload was always fine — but the template: `oya/partials/chart.html`
calls `dim()` and `new Chart()` unconditionally, and the caller must load
`oya/partials/dim.html` and the vendored Chart.js. This was the only one of
sixteen callers in the monorepo that loaded neither, and the test suite could
not see it because it asserted on `response.context` and never on the rendered
HTML. `PageRendersItsChartLibraries` is that missing assertion.
"""

from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform

from toto.company.services.ownership import issue_shares
from toto.company.views import ownership_register

from .factories import CompanyFactoryMixin


class ChartTestCase(CompanyFactoryMixin, TestCase):
    """The seeded shape the real demo company uses: 600 founder units at two
    votes each and 400 ordinary at one, so units total 1000 and votes 1600."""

    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.member = get_user_model().objects.create_user("clerk", password="x")
        self.company = self.make_company()
        self.founder = self.make_share_class(
            self.company, name="Founder", slug="founder",
            votes_per_unit=Decimal("2"))
        self.ordinary = self.make_share_class(self.company)

    def hold(self, name, share_class, units):
        party = self.make_party(self.company, name)
        issue_shares(company=self.company, target_party=party,
                     share_class=share_class, units=Decimal(units))
        return party

    def page(self, company=None):
        self.client.force_login(self.member)
        return self.client.get(reverse(
            "company:shareholders", args=[(company or self.company).slug]))


class ChartDataTests(ChartTestCase):
    def test_ownership_is_units_and_voting_is_votes(self):
        """600/1000 units is 60% of the capital; 1200/1600 votes is 75% of
        the control. The two numbers are the reason for two charts."""
        self.hold("Ada", self.founder, "600")
        self.hold("Bob", self.ordinary, "250")
        self.hold("Cleo", self.ordinary, "150")

        register = ownership_register(self.company)
        self.assertEqual(register["total_units"], Decimal("1000.000000"))
        self.assertEqual(register["total_votes"], Decimal("1600.000000"))

        ownership = json.loads(register["ownership_chart_json"])
        voting = json.loads(register["voting_chart_json"])
        self.assertEqual(ownership["datasets"][0]["data"], [600.0, 250.0, 150.0])
        self.assertEqual(voting["datasets"][0]["data"], [1200.0, 250.0, 150.0])

        summary = {row["name"]: row for row in register["shareholder_summary"]}
        self.assertEqual(summary["Ada"]["ownership_percent"], Decimal("60.0"))
        self.assertEqual(summary["Ada"]["voting_percent"], Decimal("75.0"))

    def test_a_holder_of_two_classes_is_one_slice(self):
        """A shareholder is a person, not a row: the chart answers a
        per-holder question even though the table lists per holding."""
        ada = self.hold("Ada", self.founder, "600")
        issue_shares(company=self.company, target_party=ada,
                     share_class=self.ordinary, units=Decimal("100"))
        self.hold("Bob", self.ordinary, "300")

        register = ownership_register(self.company)
        ownership = json.loads(register["ownership_chart_json"])
        self.assertEqual(ownership["labels"], ["Ada", "Bob"])
        self.assertEqual(ownership["datasets"][0]["data"], [700.0, 300.0])
        self.assertEqual(len(register["shareholder_structure"]), 3)  # per holding

    def test_the_partials_contract_is_honoured(self):
        """`chart_type`/`labels`/`datasets` at the TOP level. Chart.js's own
        `{type, data: {...}}` shape renders a blank canvas and reports
        nothing at all, because the partial reads `chart.chart_type`."""
        self.hold("Ada", self.ordinary, "10")
        chart = json.loads(ownership_register(self.company)["ownership_chart_json"])
        self.assertEqual(set(chart), {"chart_type", "labels", "datasets"})
        self.assertEqual(chart["chart_type"], "doughnut")
        self.assertNotIn("data", chart)

    def test_every_slice_gets_a_colour(self):
        """The palette holds six colours and used to be indexed directly, so
        a seventh shareholder drew with `backgroundColor: undefined`."""
        for index in range(7):
            self.hold(f"Holder {index}", self.ordinary, "10")
        chart = json.loads(ownership_register(self.company)["ownership_chart_json"])
        colours = chart["datasets"][0]["backgroundColor"]
        self.assertEqual(len(colours), 7)
        self.assertTrue(all(colour.startswith("#") for colour in colours))

    def test_the_slices_are_parties_not_labels(self):
        """Aggregation keys on the party, and the label is only a label.

        Two parties cannot share a name inside one company — the database
        says so (`bc_one_party_name_per_company`) — so this is not a live
        collision. It is keyed on the identity anyway: a chart that depends
        on another model's constraint to stay correct is one refactor away
        from merging two shareholders into one slice.
        """
        ada = self.hold("Ada", self.ordinary, "10")
        bob = self.hold("Bob", self.ordinary, "30")
        register = ownership_register(self.company)
        chart = json.loads(register["ownership_chart_json"])
        self.assertEqual(chart["labels"], ["Bob", "Ada"])  # largest first
        self.assertEqual(chart["datasets"][0]["data"], [30.0, 10.0])
        self.assertNotEqual(ada.pk, bob.pk)


class TotalsTests(ChartTestCase):
    def test_units_and_votes_are_never_rounded(self):
        self.hold("Ada", self.ordinary, "33.333333")
        register = ownership_register(self.company)
        self.assertEqual(register["total_units"], Decimal("33.333333"))
        self.assertIsInstance(register["total_units"], Decimal)

    def test_percentages_sum_to_a_hundred_for_both_metrics(self):
        self.hold("Ada", self.founder, "600")
        self.hold("Bob", self.ordinary, "250")
        self.hold("Cleo", self.ordinary, "150")
        rows = ownership_register(self.company)["shareholder_structure"]
        self.assertEqual(sum(row["ownership_percent"] for row in rows), 100)
        self.assertEqual(sum(row["voting_percent"] for row in rows), 100)


class ZeroDataTests(ChartTestCase):
    def test_a_company_with_no_holdings_charts_nothing(self):
        register = ownership_register(self.company)
        self.assertEqual(register["ownership_chart_json"], "")
        self.assertEqual(register["voting_chart_json"], "")
        self.assertEqual(register["total_units"], Decimal("0"))

    def test_the_empty_page_renders_its_empty_states(self):
        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No shares issued yet")
        self.assertContains(response, "nothing to chart")

    def test_a_voteless_class_charts_ownership_and_says_so_for_voting(self):
        """`votes_per_unit = 0` is legal (the constraint is >= 0). Ownership
        is still a real question; voting power is not, and must not divide
        by zero to say so."""
        silent = self.make_share_class(self.company, name="Silent",
                                       slug="silent",
                                       votes_per_unit=Decimal("0"))
        self.hold("Ada", silent, "100")
        register = ownership_register(self.company)
        self.assertTrue(register["ownership_chart_json"])
        self.assertEqual(register["voting_chart_json"], "")
        self.assertEqual(register["total_votes"], Decimal("0"))
        rows = register["shareholder_structure"]
        self.assertEqual(rows[0]["ownership_percent"], Decimal("100.0"))
        self.assertEqual(rows[0]["voting_percent"], Decimal("0"))

        response = self.page()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No votes attached")


class EscapingTests(ChartTestCase):
    HOSTILE = '</script><img src=x onerror="alert(1)">'

    def test_a_hostile_shareholder_name_cannot_break_out_of_the_payload(self):
        """Party names are typed by staff and only `.strip()`ed, so the name
        is untrusted text on its way into a <script> block."""
        self.hold(self.HOSTILE, self.ordinary, "10")

        chart = json.loads(ownership_register(self.company)["ownership_chart_json"])
        self.assertEqual(chart["labels"], [self.HOSTILE])  # intact as DATA

        body = self.page().content.decode()
        self.assertNotIn(self.HOSTILE, body)
        self.assertNotIn("<img src=x", body)
        # escapejs hex-escapes the characters that would end the script.
        self.assertIn("\\u003C", body)

    def test_the_page_still_shows_the_name_as_text(self):
        self.hold(self.HOSTILE, self.ordinary, "10")
        response = self.page()
        self.assertContains(response, "&lt;/script&gt;")  # escaped in the table


class PageRendersItsChartLibraries(ChartTestCase):
    """The assertion whose absence let the bug ship.

    The old suite checked `response.context[...]` only, so a page that
    produced perfect data and then failed to load the library that draws it
    passed every test while showing an empty canvas.
    """

    def setUp(self):
        super().setUp()
        self.hold("Ada", self.founder, "600")
        self.hold("Bob", self.ordinary, "400")

    def test_chart_js_is_loaded(self):
        self.assertContains(self.page(), "vendor/chartjs/chart.umd.min.js")

    def test_the_dim_helper_the_partial_calls_is_defined(self):
        """`oya/partials/chart.html` calls dim() unconditionally; without
        this the inline script dies on a ReferenceError before it reaches
        `new Chart`, and the canvas stays blank."""
        self.assertContains(self.page(), "function dim(")

    def test_both_canvases_are_on_the_page(self):
        body = self.page().content.decode()
        self.assertIn('id="company_ownership_pie"', body)
        self.assertIn('id="company_voting_pie"', body)

    def test_the_two_metrics_are_named_in_the_table(self):
        body = self.page().content.decode()
        self.assertIn("Ownership %", body)
        self.assertIn("Voting %", body)
        # "Share" alone said nothing about which of the two it meant.
        self.assertNotIn(">Share<", body)

    def test_the_payload_reaches_the_page_as_parseable_json(self):
        body = self.page().content.decode()
        self.assertIn("JSON.parse(", body)
        self.assertIn("doughnut", body)
