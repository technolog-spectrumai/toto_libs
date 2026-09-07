"""Adding a member: the department dropdown must accept what it offers.

The form's own queryset already restricts `primary_department` to departments
of THIS company, so every option on that dropdown is valid by construction.
The bug was that picking one was refused anyway — see the test below for why.
"""

from __future__ import annotations

from django.test import TestCase

from toto.company.forms import CompanyMembershipForm
from toto.company.models import CompanyMembership, Department
from toto.people.models import Person

from .factories import CompanyFactoryMixin


class MembershipFormTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.board = Department.objects.create(company=self.company, name="Board")
        self.person = Person.objects.create(display_name="Eleanor Voss",
                                            email="eleanor@example.com")

    def _data(self, **over):
        data = {"person": self.person.pk, "job_title": "",
                "primary_department": self.board.pk,
                "joined_on": "2026-09-07", "note": ""}
        data.update(over)
        return data

    def test_a_department_of_this_company_is_accepted(self):
        """The regression.

        `CompanyMembership.clean` compares `primary_department.company_id` to
        `self.company_id` — but the form only assigns `company` in `save()`,
        and Django runs `instance.full_clean()` in `_post_clean`, BEFORE that.
        So company_id was None on every add, and the one department the
        dropdown offered was rejected as belonging to another company.
        """
        form = CompanyMembershipForm(self._data(), company=self.company)
        self.assertTrue(form.is_valid(), dict(form.errors))

    def test_the_membership_saves_against_the_right_company(self):
        form = CompanyMembershipForm(self._data(), company=self.company)
        self.assertTrue(form.is_valid(), dict(form.errors))
        membership = form.save()
        self.assertEqual(membership.company, self.company)
        self.assertEqual(membership.primary_department, self.board)

    def test_no_department_is_still_fine(self):
        """The field is optional — a member need not sit in a department."""
        form = CompanyMembershipForm(self._data(primary_department=""),
                                     company=self.company)
        self.assertTrue(form.is_valid(), dict(form.errors))
        self.assertIsNone(form.save().primary_department)

    def test_another_company_s_department_is_not_offered(self):
        """The queryset is the real guard, and it still holds."""
        other = self.make_company(name="Somebody Else Ltd")
        theirs = Department.objects.create(company=other, name="Board")
        form = CompanyMembershipForm(company=self.company)
        self.assertNotIn(
            theirs.pk,
            form.fields["primary_department"].queryset.values_list("pk", flat=True))

    def test_a_foreign_department_is_still_refused_if_forced(self):
        """Belt and braces: the model check must survive a hand-made POST."""
        other = self.make_company(name="Somebody Else Ltd")
        theirs = Department.objects.create(company=other, name="Board")
        form = CompanyMembershipForm(self._data(primary_department=theirs.pk),
                                     company=self.company)
        self.assertFalse(form.is_valid())

    def test_a_duplicate_active_member_is_refused(self):
        CompanyMembership.objects.create(company=self.company, person=self.person)
        form = CompanyMembershipForm(self._data(), company=self.company)
        self.assertFalse(form.is_valid())


class OrgChartPeopleTests(CompanyFactoryMixin, TestCase):
    """"Show people" must draw the members the Members card actually creates.

    Two parallel models put a human in a department: `DepartmentMembership`
    (a Party) and `CompanyMembership.primary_department` (a Person). The chart
    drew only the first, so a company whose members were added on the structure
    page — which is all of them — showed an empty chart with the box ticked.
    """

    def setUp(self):
        from toto.company.views import organization_graph

        self.graph = organization_graph
        self.company = self.make_company()
        self.board = Department.objects.create(company=self.company, name="Board")

    def _member(self, name, department=None):
        person = Person.objects.create(display_name=name,
                                       email=f"{name.lower()}@example.com")
        CompanyMembership.objects.create(
            company=self.company, person=person,
            primary_department=department or self.board, job_title="Director")
        return person

    def _people(self, graph):
        return {n["label"] for n in graph["nodes"] if n["type"] == "person"}

    def test_a_company_member_appears_in_the_chart(self):
        self._member("Eleanor Voss")
        graph = self.graph(self.company, include_members=True)
        self.assertIn("Eleanor Voss", self._people(graph))

    def test_they_hang_off_their_primary_department(self):
        person = self._member("Eleanor Voss")
        graph = self.graph(self.company, include_members=True)
        edge = next(e for e in graph["edges"]
                    if e["target"] == f"person-{person.pk}")
        self.assertEqual(edge["source"], f"department-{self.board.pk}")
        self.assertEqual(edge["type"], "member")

    def test_people_stay_out_unless_asked_for(self):
        """The checkbox still means something."""
        self._member("Eleanor Voss")
        self.assertEqual(self._people(self.graph(self.company)), set())

    def test_a_member_with_no_department_is_not_drawn(self):
        """Nothing to hang them off — the chart is departments and their people."""
        person = Person.objects.create(display_name="Nowhere",
                                       email="n@example.com")
        CompanyMembership.objects.create(company=self.company, person=person)
        graph = self.graph(self.company, include_members=True)
        self.assertNotIn("Nowhere", self._people(graph))

    def test_an_inactive_member_is_not_drawn(self):
        person = Person.objects.create(display_name="Former",
                                       email="f@example.com")
        CompanyMembership.objects.create(
            company=self.company, person=person,
            primary_department=self.board, active=False)
        graph = self.graph(self.company, include_members=True)
        self.assertNotIn("Former", self._people(graph))

    def test_somebody_recorded_twice_appears_once(self):
        """Party head AND company member: one node, not two."""
        from toto.company.models import Party

        person = Person.objects.create(display_name="Dorotea Farfarele",
                                       email="d@example.com")
        party = Party.objects.create(company=self.company,
                                     name="Dorotea Farfarele", person=person)
        self.board.head = party
        self.board.save()
        CompanyMembership.objects.create(
            company=self.company, person=person,
            primary_department=self.board, job_title="Chair")

        graph = self.graph(self.company, include_members=True)
        labels = [n["label"] for n in graph["nodes"] if n["type"] == "person"]
        self.assertEqual(labels.count("Dorotea Farfarele"), 1)
