"""The cross-project wiki search.

The load-bearing tests here are the scoping ones. This view is the first thing
on the platform that reads wiki pages across project boundaries — every other
wiki route is `project/<pk>/…` and checks one project — so "you only ever see
pages from projects you belong to" is the property that must not regress, and
it is tested through all three membership routes rather than through the staff
bypass, which would prove nothing about membership at all.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person

from .models import (
    DocumentationPage, Practitioner, Project, ProjectCommitment,
)

User = get_user_model()


def make_person(username):
    user = User.objects.create_user(username, password="pw")
    return user, Person.objects.create(user=user, display_name=username.title())


class WikiSearchTests(TestCase):
    def setUp(self):
        # Every page here renders through PageProcessor, which 404s without an
        # active platform row — so a missing fixture looks exactly like a
        # permission denial. Seed it first.
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2026, active=True
        )
        self.me, self.my_person = make_person("member")
        self.stranger, self.stranger_person = make_person("stranger")
        _, self.lead = make_person("lead")

        # Two projects with the same lead: mine is the one I get let into,
        # theirs is the control. Same lead on both so that membership — not
        # some accident of who created what — is the only difference.
        self.mine = Project.objects.create(name="Mine", project_lead=self.lead)
        self.theirs = Project.objects.create(name="Theirs", project_lead=self.lead)

        self.page = DocumentationPage.objects.create(
            project=self.mine, title="Deployment runbook",
            body_html="<p>restart the <b>worker</b> first</p>")
        # Same words, other project. A scoping bug that filters in the template
        # rather than the queryset shows up here as a leaked row.
        self.secret = DocumentationPage.objects.create(
            project=self.theirs, title="Their runbook",
            body_html="<p>restart the worker first</p>")

    def commit(self, person, project):
        """Membership by commitment — the ordinary route onto a project."""
        practitioner = Practitioner.objects.create(person=person)
        ProjectCommitment.objects.create(
            practitioner=practitioner, project=project,
            hours_per_day=Decimal("4.00"))
        return practitioner

    def get(self, **params):
        return self.client.get(reverse("kanban:wiki_search"), params)

    # ── the scope ────────────────────────────────────────────────────────────

    def test_it_needs_a_login(self):
        self.assertNotEqual(self.get().status_code, 200)

    def test_a_stranger_sees_nothing(self):
        self.client.force_login(self.stranger)
        response = self.get(q="runbook")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context["rows"]), [])
        self.assertEqual(response.context["total"], 0)
        self.assertNotContains(response, "Deployment runbook")

    def test_a_member_sees_their_project_and_not_the_other_one(self):
        # The whole feature in one assertion: both pages match the words, only
        # one is mine.
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        titles = [r["page"].title for r in self.get(q="runbook").context["rows"]]
        self.assertEqual(titles, ["Deployment runbook"])

    def test_a_project_lead_can_search_their_own_project(self):
        # A lead who is not a practitioner can READ but cannot write; the search
        # follows reading, so they must be here.
        lead_user = self.lead.user
        self.client.force_login(lead_user)
        titles = [r["page"].title for r in self.get(q="runbook").context["rows"]]
        self.assertEqual(sorted(titles), ["Deployment runbook", "Their runbook"])

    def test_an_auditor_can_search_the_project_they_audit(self):
        practitioner = Practitioner.objects.create(person=self.my_person)
        self.mine.auditors.add(practitioner)
        self.client.force_login(self.me)
        titles = [r["page"].title for r in self.get(q="runbook").context["rows"]]
        self.assertEqual(titles, ["Deployment runbook"])

    def test_an_ended_commitment_ends_the_access(self):
        practitioner = self.commit(self.my_person, self.mine)
        practitioner.commitments.update(is_active=False)
        self.client.force_login(self.me)
        self.assertEqual(list(self.get(q="runbook").context["rows"]), [])

    def test_membership_in_one_project_does_not_open_another(self):
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        response = self.get(q="Their")
        self.assertEqual(list(response.context["rows"]), [])
        self.assertNotContains(response, "Their runbook")

    def test_a_member_is_counted_once_per_page(self):
        # Two membership routes onto the same project. The permission query ORs
        # across joins, so without .distinct() this page appears twice.
        practitioner = self.commit(self.my_person, self.mine)
        self.mine.auditors.add(practitioner)
        self.client.force_login(self.me)
        response = self.get(q="runbook")
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["total"], 1)

    # ── the search ───────────────────────────────────────────────────────────

    def test_it_finds_pages_by_title(self):
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        self.assertTrue(self.get(q="Deployment").context["rows"])

    def test_it_finds_pages_by_body_text(self):
        # The reason to search bodies at all: you remember a sentence, not a
        # heading.
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        self.assertTrue(self.get(q="restart the").context["rows"])

    def test_a_phrase_broken_by_markup_does_not_match(self):
        # A KNOWN limitation, pinned rather than hidden: the search runs over
        # `body_html`, so "restart the worker" misses a body that spells it
        # "restart the <b>worker</b>". Fixing it needs a plain-text shadow
        # column written alongside body_html by the cyprian bridge — a schema
        # change, not a query change. Until then this is the honest behaviour,
        # and a test that asserted otherwise would be asserting a wish.
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        self.assertEqual(list(self.get(q="restart the worker").context["rows"]), [])

    def test_a_search_that_matches_nothing_still_reports_a_count(self):
        # A Page defines __len__, so an empty result page is falsy; a view that
        # tests it for truth reports "no search was run" on exactly the search
        # that most needs to say "nothing matched".
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        response = self.get(q="nothing here matches this")
        self.assertEqual(response.context["total"], 0)
        self.assertEqual(response.context["query"], "nothing here matches this")

    def test_the_snippet_is_plain_text(self):
        # Cut from text, never from markup: slicing body_html mid-tag produces
        # broken HTML, and a page must not restyle the list it appears in.
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        row = self.get(q="worker").context["rows"][0]
        self.assertIn("worker", row["snippet"])
        self.assertNotIn("<b>", row["snippet"])
        self.assertNotIn("<p>", row["snippet"])

    def test_no_query_shows_recent_pages_rather_than_a_blank_page(self):
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        response = self.get()
        self.assertEqual(response.context["query"], "")
        self.assertTrue(response.context["rows"], "the landing page should not be empty")
        self.assertIsNone(response.context["total"])

    def test_a_blank_query_is_not_a_search(self):
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        response = self.get(q="   ")
        self.assertEqual(response.context["query"], "")
        self.assertIsNone(response.context["total"])

    # ── the rows ─────────────────────────────────────────────────────────────

    def test_each_row_says_which_project_it_came_from(self):
        # The one thing a cross-project list must show that a per-board list
        # never had to.
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        self.assertEqual(self.get(q="Deployment").context["rows"][0]["project"],
                         self.mine)

    def test_a_row_links_to_the_page_on_its_own_board(self):
        self.commit(self.my_person, self.mine)
        self.client.force_login(self.me)
        self.assertEqual(
            self.get(q="Deployment").context["rows"][0]["url"],
            reverse("kanban:wiki_page", args=[self.mine.pk, self.page.slug]))
