"""Ownership must be exact to the last unit, and its history append-only.

Irena's OwnershipServiceTests, kept, plus the exactness and multi-company
isolation cases the brief calls for by name.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from toto.company.models import OwnershipEvent, OwnershipEventType, ShareHolding
from toto.company.services.ownership import (
    issue_shares,
    record_shareholding,
    split_share_class,
    transfer_shares,
)
from toto.company.tests.factories import CompanyFactoryMixin


class OwnershipServiceTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.share_class = self.make_share_class(self.company)
        self.ada = self.make_party(self.company, "Ada")
        self.bob = self.make_party(self.company, "Bob")

    def _live(self, party):
        return ShareHolding.objects.get(
            party=party, share_class=self.share_class, until__isnull=True,
        )

    def test_issue_then_transfer_moves_exactly_what_was_asked(self):
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("100"))
        self.assertEqual(self._live(self.ada).units, Decimal("100"))

        transfer_shares(company=self.company, source_party=self.ada,
                        target_party=self.bob, share_class=self.share_class,
                        units=Decimal("40"))
        self.assertEqual(self._live(self.ada).units, Decimal("60"))
        self.assertEqual(self._live(self.bob).units, Decimal("40"))

    def test_transfer_rejects_insufficient_shares(self):
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("10"))
        with self.assertRaises(ValidationError):
            transfer_shares(company=self.company, source_party=self.ada,
                            target_party=self.bob, share_class=self.share_class,
                            units=Decimal("11"))
        # The refusal must leave the register untouched, not half-moved.
        self.assertEqual(self._live(self.ada).units, Decimal("10"))
        self.assertFalse(ShareHolding.objects.filter(party=self.bob).exists())

    def test_fractional_units_survive_a_transfer_exactly(self):
        """Six decimal places, and no float anywhere in the path."""
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("0.000003"))
        transfer_shares(company=self.company, source_party=self.ada,
                        target_party=self.bob, share_class=self.share_class,
                        units=Decimal("0.000001"))
        self.assertEqual(self._live(self.ada).units, Decimal("0.000002"))
        self.assertEqual(self._live(self.bob).units, Decimal("0.000001"))

    def test_the_register_sums_to_what_was_issued(self):
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("33.333333"))
        issue_shares(company=self.company, target_party=self.bob,
                     share_class=self.share_class, units=Decimal("66.666667"))
        total = sum(
            holding.units for holding in
            ShareHolding.objects.filter(share_class=self.share_class, until__isnull=True)
        )
        self.assertEqual(total, Decimal("100.000000"))

    def test_split_rewrites_live_holdings_without_losing_history(self):
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("10"))
        split_share_class(company=self.company, source_share_class=self.share_class,
                          target_share_class=self.share_class,
                          numerator=Decimal("3"), denominator=Decimal("1"))
        self.assertEqual(self._live(self.ada).units, Decimal("30"))
        # The closed rows stay: history is never rewritten in place.
        self.assertTrue(
            ShareHolding.objects.filter(party=self.ada, until__isnull=False).exists()
        )

    def test_record_shareholding_sets_the_position_exactly(self):
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("10"))
        record_shareholding(company=self.company, party=self.ada,
                            share_class=self.share_class, units=Decimal("7.5"))
        self.assertEqual(self._live(self.ada).units, Decimal("7.500000"))
        correction = OwnershipEvent.objects.filter(
            event_type=OwnershipEventType.CORRECTION,
        ).latest("recorded_at")
        self.assertEqual(correction.source_units, Decimal("10.000000"))
        self.assertEqual(correction.target_units, Decimal("7.500000"))

    def test_zero_and_negative_units_are_refused(self):
        for bad in (Decimal("0"), Decimal("-1")):
            with self.assertRaises(ValidationError):
                issue_shares(company=self.company, target_party=self.ada,
                             share_class=self.share_class, units=bad)


class OwnershipEventsAreAppendOnly(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.share_class = self.make_share_class(self.company)
        self.ada = self.make_party(self.company, "Ada")
        issue_shares(company=self.company, target_party=self.ada,
                     share_class=self.share_class, units=Decimal("5"))
        self.event = OwnershipEvent.objects.latest("recorded_at")

    def test_an_event_cannot_be_updated(self):
        self.event.note = "rewritten"
        with self.assertRaises(ValidationError):
            self.event.save()

    def test_an_event_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.event.delete()


class CompaniesAreIsolated(CompanyFactoryMixin, TestCase):
    """Several companies, no multi-tenancy — but no leakage either."""

    def setUp(self):
        self.one = self.make_company("One sp. z o.o.")
        self.two = self.make_company("Two P.S.A.")
        self.one_class = self.make_share_class(self.one)
        self.two_class = self.make_share_class(self.two)
        self.one_party = self.make_party(self.one, "Ada")
        self.two_party = self.make_party(self.two, "Ada")

    def test_a_party_cannot_hold_another_companys_share_class(self):
        with self.assertRaises(ValidationError):
            issue_shares(company=self.one, target_party=self.one_party,
                         share_class=self.two_class, units=Decimal("1"))

    def test_a_transfer_cannot_cross_companies(self):
        issue_shares(company=self.one, target_party=self.one_party,
                     share_class=self.one_class, units=Decimal("5"))
        with self.assertRaises(ValidationError):
            transfer_shares(company=self.one, source_party=self.one_party,
                            target_party=self.two_party,
                            share_class=self.one_class, units=Decimal("1"))

    def test_the_same_shareholder_name_in_two_companies_is_two_registers(self):
        issue_shares(company=self.one, target_party=self.one_party,
                     share_class=self.one_class, units=Decimal("5"))
        issue_shares(company=self.two, target_party=self.two_party,
                     share_class=self.two_class, units=Decimal("9"))
        self.assertEqual(
            ShareHolding.objects.get(party=self.one_party, until__isnull=True).units,
            Decimal("5.000000"),
        )
        self.assertEqual(
            ShareHolding.objects.get(party=self.two_party, until__isnull=True).units,
            Decimal("9.000000"),
        )
