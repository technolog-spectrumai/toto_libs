"""The seeder: creates the rule, never clobbers staff edits, repairs the unit."""

from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from toto.tariffs.models import BillingUnit

from ..models import TaxRule


def run_ingress():
    out = StringIO()
    call_command("ingress_tax", stdout=out)
    return out.getvalue()


class IngressTaxTests(TestCase):
    def test_seeds_the_storage_rule_and_reports_it_free(self):
        output = run_ingress()

        rule = TaxRule.objects.get(metric_code="storage.gb_day")
        self.assertEqual(rule.allowance, Decimal("1"))
        self.assertEqual(rule.unit_label, "GB")
        self.assertTrue(rule.active)
        self.assertIn("FREE", output)

    def test_rerun_preserves_a_staff_edit(self):
        run_ingress()
        TaxRule.objects.filter(metric_code="storage.gb_day").update(
            allowance=Decimal("5"))

        run_ingress()

        rule = TaxRule.objects.get(metric_code="storage.gb_day")
        self.assertEqual(rule.allowance, Decimal("5"))

    def test_repairs_the_billing_unit_mirror(self):
        # What billing_unit_for() creates when the tariffs seeder mirrors the
        # metric: an uppercased label and no dimension.
        BillingUnit.objects.create(code="gb_day", label="GB_DAY", dimension="",
                                   app_label="tariffs", active=True)

        run_ingress()

        unit = BillingUnit.objects.get(code="gb_day")
        self.assertEqual(unit.label, "Gigabyte-day")
        self.assertEqual(unit.dimension, "storage_time")

    def test_seeds_the_time_hold_rule(self):
        output = run_ingress()

        rule = TaxRule.objects.get(metric_code="time.hold")
        self.assertEqual(rule.allowance, Decimal("0"))
        self.assertEqual(rule.unit_label, "h")
        self.assertTrue(rule.active)
        self.assertIn("time.hold", output)

    def test_repairs_the_hour_day_unit(self):
        BillingUnit.objects.create(code="hour_day", label="HOUR_DAY",
                                   dimension="", app_label="tariffs", active=True)

        run_ingress()

        unit = BillingUnit.objects.get(code="hour_day")
        self.assertEqual(unit.label, "Hour-day")
        self.assertEqual(unit.dimension, "time_hold")
