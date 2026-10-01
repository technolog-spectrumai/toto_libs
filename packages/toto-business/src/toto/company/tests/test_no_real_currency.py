"""No living currency in the company register (economy.md, "No real currency,
anywhere", 2026-09-30).

The rule was written for the assets app, but it reaches every default, seed
and example on the platform: the share capital is a quoted amount, so its unit
is a platform unit — the Florin, FLOR — and never a złoty, a dollar or a euro.
The default sat at "PLN" until 2026-09-30; these tests keep it from drifting
back, in the model, in the migration that created the column and in the demo
company `ingress_company --full` seeds — including one seeded before that
date, which the re-run every deploy performs must bring up to date.
"""

from __future__ import annotations

import re
import tempfile
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.company.management.commands.ingress_company import COMPANY_NAME
from toto.company.models import Company

MEDIA = tempfile.mkdtemp(prefix="company-currency-")

#: The same shape as toto.assets.tests: a living currency by code, name or
#: symbol. Historic coins (the Florin, the Assarion) are fine.
REAL_CURRENCY = re.compile(
    r"\b(pln|usd|eur|euros?|gbp|chf|jpy|tpln|dollars?)\b|z[łl]oty|zł|"
    r"\$|€|£", re.IGNORECASE)


class NoRealCurrencyTests(TestCase):

    def test_the_default_capital_unit_is_the_florin(self):
        field = Company._meta.get_field("capital_currency")
        self.assertEqual(field.default, "FLOR")
        self.assertIsNone(REAL_CURRENCY.search(field.default))

        company = Company.objects.create(name="Fresh")
        self.assertEqual(company.capital_currency, "FLOR")

    def test_the_initial_migration_agrees_with_the_model(self):
        """0001 was edited in place (a default has no schema effect), so the
        autodetector must have nothing left to write for this app."""
        out = StringIO()
        try:
            call_command("makemigrations", "company", check=True, dry_run=True,
                         stdout=out, stderr=out)
        except SystemExit:
            self.fail(f"the company migrations are out of step:\n{out.getvalue()}")

    @override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
    def test_the_seeded_company_carries_no_living_currency(self):
        call_command("ingress_company", "--full", stdout=StringIO())

        company = Company.objects.get()
        self.assertEqual(company.capital_currency, "FLOR")
        blob = " ".join([
            company.name, company.legal_form_label, company.capital_currency,
            company.note, company.description,
        ])
        self.assertIsNone(REAL_CURRENCY.search(blob), blob)

    @override_settings(MEDIA_ROOT=MEDIA, VAULT_ROOT=MEDIA)
    def test_a_rerun_leaves_a_unit_chosen_by_hand_alone(self):
        """A unit somebody set on the demo company is a decision, and a
        deploy must not undo it."""
        Company.objects.create(name=COMPANY_NAME, capital_currency="ASR")

        out = StringIO()
        call_command("ingress_company", "--full", stdout=out)

        self.assertEqual(
            Company.objects.get(name=COMPANY_NAME).capital_currency, "ASR")
