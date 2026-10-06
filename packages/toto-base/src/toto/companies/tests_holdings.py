"""Holdings and their rules (stage 65): one per company and person, whole
and never negative, by the database itself and at the door.

    manage.py test toto.companies.tests_holdings
"""

from unittest import mock

from django.db import IntegrityError, transaction

from toto.companies.models import CompanyRecord, ShareHolding
from toto.companies.register import parse_quantity
from toto.companies.testing import CompaniesTestCase, audit_records, client_of


class ConstraintTests(CompaniesTestCase):
    def test_one_holding_per_company_and_person_by_the_database(self):
        self.hold_shares(self.acme, self.hold, 10)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.hold_shares(self.acme, self.hold, 3)
        self.assertEqual(ShareHolding.objects.filter(community=self.acme).count(), 1)

    def test_bulk_create_cannot_get_a_second_row_past_it(self):
        self.hold_shares(self.acme, self.hold, 10)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareHolding.objects.bulk_create(
                [ShareHolding(community=self.acme, person=self.hold, quantity=1)])

    def test_one_person_may_hold_in_two_companies(self):
        self.hold_shares(self.acme, self.hold, 10)
        self.hold_shares(self.globex, self.hold, 7)
        self.assertEqual(ShareHolding.objects.filter(person=self.hold).count(), 2)

    def test_different_people_hold_different_quantities(self):
        self.hold_shares(self.acme, self.hold, 10)
        self.hold_shares(self.acme, self.mia, 250)
        self.hold_shares(self.acme, self.stan, 0)
        self.assertEqual(
            sorted(ShareHolding.objects.filter(community=self.acme)
                   .values_list("quantity", flat=True)), [0, 10, 250])

    def test_a_negative_quantity_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.hold_shares(self.acme, self.hold, -1)
        self.assertFalse(ShareHolding.objects.exists())

    def test_an_update_cannot_make_one_negative(self):
        holding = self.hold_shares(self.acme, self.hold, 4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            ShareHolding.objects.filter(pk=holding.pk).update(quantity=-4)
        holding.refresh_from_db()
        self.assertEqual(holding.quantity, 4)

    def test_zero_is_a_quantity(self):
        self.assertEqual(self.hold_shares(self.acme, self.hold, 0).quantity, 0)

    def test_both_rules_are_named_constraints_of_the_table(self):
        names = {constraint.name for constraint in ShareHolding._meta.constraints}
        self.assertEqual(names, {"companies_one_holding_per_person",
                                 "companies_holding_not_negative"})

    def test_the_quantity_is_a_whole_number_column(self):
        self.assertEqual(ShareHolding._meta.get_field("quantity").get_internal_type(),
                         "PositiveBigIntegerField")

    def test_a_holding_links_a_community_and_a_person_and_no_membership(self):
        targets = {field.name: field.related_model._meta.label
                   for field in ShareHolding._meta.get_fields() if field.is_relation}
        self.assertEqual(targets, {"community": "socialhub.Community",
                                   "person": "people.Person"})

    def test_the_register_goes_with_the_company(self):
        self.hold_shares(self.globex, self.hold, 5)
        CompanyRecord.objects.create(community=self.globex, id_number="0001")
        self.globex.delete()
        self.assertFalse(ShareHolding.objects.exists())
        self.assertFalse(CompanyRecord.objects.exists())

    def test_a_holding_goes_with_its_person(self):
        self.hold_shares(self.acme, self.stan, 5)
        self.stan_user.delete()
        self.assertFalse(ShareHolding.objects.filter(community=self.acme).exists())

    def test_one_record_per_company(self):
        CompanyRecord.objects.create(community=self.acme, id_number="1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CompanyRecord.objects.create(community=self.acme, id_number="2")

    def test_the_id_number_is_text(self):
        self.assertEqual(CompanyRecord._meta.get_field("id_number").get_internal_type(),
                         "CharField")

    def test_neither_table_is_in_the_django_admin(self):
        from django.contrib import admin

        registered = {model._meta.label for model in admin.site._registry}
        self.assertNotIn("companies.ShareHolding", registered)
        self.assertNotIn("companies.CompanyRecord", registered)

    def test_socialhub_got_no_company_column(self):
        from toto.socialhub.models import Community

        columns = {field.column for field in Community._meta.concrete_fields}
        self.assertFalse({column for column in columns
                          if "share" in column or "id_number" in column
                          or "company" in column})


class WholeNumberTests(CompaniesTestCase):
    def test_what_is_a_whole_non_negative_number(self):
        for text, number in (("0", 0), ("7", 7), (" 12 ", 12), ("007", 7),
                             ("9" * 18, 10 ** 18 - 1)):
            with self.subTest(text=text):
                self.assertEqual(parse_quantity(text), number)

    def test_what_is_not(self):
        for text in ("", " ", None, "-1", "+1", "1.5", "1,5", "1.0", "1e3", "1 000",
                     "１２", "0x10", "ten", "1\n2", "9" * 19, "٣", "1_000", "--1"):
            with self.subTest(text=text):
                self.assertIsNone(parse_quantity(text))


class HoldingDoorTests(CompaniesTestCase):
    def quantities(self, company=None):
        return dict(ShareHolding.objects.filter(community=company or self.acme)
                    .values_list("person__slug", "quantity"))

    def test_the_head_records_a_holding(self):
        response = self.record(self.acme, self.hold, 40)
        self.assertEqual(response.status_code, 302)
        self.assertIn("tab=shareholdings", response["Location"])
        self.assertEqual(self.quantities(), {"hold": 40})

    def test_recording_the_same_person_again_changes_the_one_row(self):
        self.record(self.acme, self.hold, 40)
        self.record(self.acme, self.hold, 55)
        self.assertEqual(self.quantities(), {"hold": 55})
        self.assertEqual(ShareHolding.objects.count(), 1)

    def test_zero_is_recorded_and_kept_as_a_row(self):
        self.record(self.acme, self.hold, 0)
        self.assertEqual(self.quantities(), {"hold": 0})

    def test_the_largest_quantity_fits(self):
        self.record(self.acme, self.hold, "9" * 18)
        self.assertEqual(self.quantities(), {"hold": 10 ** 18 - 1})

    def test_a_quantity_that_is_no_whole_number_writes_nothing(self):
        for typed in ("", "-3", "1.5", "2,5", "abc", "1e3", "１２", "9" * 19, " "):
            with self.subTest(typed=typed):
                response = self.record(self.acme, self.hold, typed)
                self.assertEqual(response.status_code, 302)
                self.assertFalse(ShareHolding.objects.exists())

    def test_a_bad_quantity_leaves_an_existing_holding_as_it_was(self):
        self.record(self.acme, self.hold, 40)
        self.record(self.acme, self.hold, "-1")
        self.record(self.acme, self.hold, "41.5")
        self.assertEqual(self.quantities(), {"hold": 40})

    def test_the_refusal_is_said_on_the_page(self):
        client = client_of(self.head_user)
        response = client.post(self.holding_url(self.acme),
                               {"person": self.hold.slug, "quantity": "1.5"}, follow=True)
        self.assertContains(response, "whole number of shares")

    def test_an_unknown_person_writes_nothing(self):
        for slug in ("nobody-here", "", "  "):
            with self.subTest(slug=slug):
                response = client_of(self.head_user).post(
                    self.holding_url(self.acme), {"person": slug, "quantity": "5"})
                self.assertEqual(response.status_code, 302)
                self.assertFalse(ShareHolding.objects.exists())

    def test_a_person_is_found_by_profile_slug_never_by_id(self):
        client_of(self.head_user).post(
            self.holding_url(self.acme), {"person": str(self.hold.pk), "quantity": "5"})
        self.assertFalse(ShareHolding.objects.exists())

    def test_the_holder_need_not_be_a_member(self):
        self.assertFalse(self.hold.communities.filter(pk=self.acme.pk).exists())
        self.record(self.acme, self.hold, 9)
        self.assertEqual(self.quantities(), {"hold": 9})

    def test_removing_takes_the_row_out(self):
        holding = self.hold_shares(self.acme, self.hold, 9)
        response = client_of(self.head_user).post(self.delete_url(self.acme, holding.pk))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ShareHolding.objects.exists())

    def test_two_managers_at_once_make_one_row(self):
        """The other manager's row lands between this door's look and its
        insert: the door changes that row instead of failing or doubling."""
        real = ShareHolding.objects.select_for_update
        test = self
        raced = []

        class Late:
            def filter(self, **kwargs):
                return self

            def first(self):
                test.hold_shares(test.acme, test.hold, 5)
                return None

        def racing(*args, **kwargs):
            if not raced:
                raced.append(True)
                return Late()
            return real(*args, **kwargs)

        with mock.patch.object(ShareHolding.objects, "select_for_update",
                               side_effect=racing):
            response = self.record(self.acme, self.hold, 9)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(raced, [True])
        self.assertEqual(self.quantities(), {"hold": 9})
        self.assertEqual(ShareHolding.objects.count(), 1)
        actions = [record.action for record in audit_records()]
        if actions:
            self.assertEqual(actions, ["COMPANIES.HOLDING.CHANGED"])

    def test_a_holding_in_one_company_is_not_one_in_another(self):
        self.record(self.acme, self.hold, 9)
        self.assertEqual(self.quantities(self.globex), {})
