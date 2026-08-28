"""Departments, the hierarchy, and company membership."""

from __future__ import annotations

import datetime as dt

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.test import TestCase

from toto.company.models import CompanyMembership, Department, DepartmentMembership
from toto.company.tests.factories import CompanyFactoryMixin
from toto.people.models import Person


class DepartmentHierarchyTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.root = Department.objects.create(company=self.company, name="Board")
        self.child = Department.objects.create(
            company=self.company, name="Finance", parent=self.root,
        )

    def test_slug_is_derived_when_absent(self):
        self.assertEqual(self.child.slug, "finance")

    def test_ancestors_are_root_first(self):
        grandchild = Department.objects.create(
            company=self.company, name="Payroll", parent=self.child,
        )
        self.assertEqual(
            [d.name for d in grandchild.ancestors()], ["Board", "Finance"],
        )

    def test_a_department_cannot_be_its_own_parent(self):
        self.child.parent = self.child
        with self.assertRaises(ValidationError):
            self.child.save()

    def test_a_cycle_is_refused(self):
        """Board -> Finance already exists; pointing Board at Finance closes a loop."""
        self.root.parent = self.child
        with self.assertRaises(ValidationError):
            self.root.save()

    def test_a_deep_cycle_is_refused(self):
        grandchild = Department.objects.create(
            company=self.company, name="Payroll", parent=self.child,
        )
        self.root.parent = grandchild
        with self.assertRaises(ValidationError):
            self.root.save()

    def test_a_parent_from_another_company_is_refused(self):
        other = self.make_company("Other sp. z o.o.")
        foreign = Department.objects.create(company=other, name="Board")
        self.child.parent = foreign
        with self.assertRaises(ValidationError):
            self.child.save()

    def test_a_head_from_another_company_is_refused(self):
        other = self.make_company("Other sp. z o.o.")
        foreign_party = self.make_party(other, "Outsider")
        self.child.head = foreign_party
        with self.assertRaises(ValidationError):
            self.child.save()

    def test_two_departments_cannot_share_a_slug_in_one_company(self):
        with self.assertRaises(IntegrityError):
            Department.objects.create(
                company=self.company, name="Finance again", slug="finance",
            )

    def test_two_companies_may_each_have_the_same_slug(self):
        other = self.make_company("Other sp. z o.o.")
        Department.objects.create(company=other, name="Finance")
        self.assertEqual(Department.objects.filter(slug="finance").count(), 2)


class DepartmentMembershipTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.department = Department.objects.create(company=self.company, name="Finance")
        self.party = self.make_party(self.company, "Ada")

    def test_a_member_must_belong_to_the_same_company(self):
        other = self.make_company("Other sp. z o.o.")
        outsider = self.make_party(other, "Outsider")
        membership = DepartmentMembership(department=self.department, party=outsider)
        with self.assertRaises(ValidationError):
            membership.save()

    def test_one_active_seat_per_party_and_department(self):
        DepartmentMembership.objects.create(department=self.department, party=self.party)
        with self.assertRaises(IntegrityError):
            DepartmentMembership.objects.create(department=self.department, party=self.party)

    def test_a_closed_seat_does_not_block_a_new_one(self):
        first = DepartmentMembership.objects.create(
            department=self.department, party=self.party,
        )
        first.active = False
        first.left_on = dt.date.today()
        first.save()
        again = DepartmentMembership.objects.create(
            department=self.department, party=self.party,
        )
        self.assertTrue(again.active)


class CompanyMembershipTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.department = Department.objects.create(company=self.company, name="Finance")
        user = get_user_model().objects.create_user("ada", password="x")
        self.person = Person.objects.create(user=user, display_name="Ada Lovelace")

    def test_a_membership_carries_a_plain_text_job_title(self):
        membership = CompanyMembership.objects.create(
            company=self.company, person=self.person,
            job_title="Chief Engineer", primary_department=self.department,
        )
        self.assertEqual(membership.job_title, "Chief Engineer")
        self.assertEqual(membership.primary_department, self.department)

    def test_the_primary_department_must_belong_to_the_company(self):
        other = self.make_company("Other sp. z o.o.")
        foreign = Department.objects.create(company=other, name="Board")
        membership = CompanyMembership(
            company=self.company, person=self.person, primary_department=foreign,
        )
        with self.assertRaises(ValidationError):
            membership.save()

    def test_one_active_membership_per_person_and_company(self):
        CompanyMembership.objects.create(company=self.company, person=self.person)
        with self.assertRaises(IntegrityError):
            CompanyMembership.objects.create(company=self.company, person=self.person)

    def test_the_same_person_may_belong_to_two_companies(self):
        other = self.make_company("Other sp. z o.o.")
        CompanyMembership.objects.create(company=self.company, person=self.person)
        CompanyMembership.objects.create(company=other, person=self.person)
        self.assertEqual(
            CompanyMembership.objects.filter(person=self.person, active=True).count(), 2,
        )

    def test_people_owns_the_person_and_does_not_know_about_this(self):
        """The link is one-way: deleting is PROTECTed, and Person has no
        Business Center field of its own."""
        CompanyMembership.objects.create(company=self.company, person=self.person)
        field_names = {f.name for f in Person._meta.get_fields()}
        self.assertNotIn("company", field_names)
        self.assertIn("bc_company_memberships", field_names)
