"""Holding plaintext drains security mana — clamped, and never a freeze."""

from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import services
from toto.tariffs.models import TariffItem
from toto.tax import services as tax
from toto.tax.models import TaxArrearsCase, TaxRule
from toto.tax.tests.factories import GB, make_vault_file

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)


@override_settings(**MASTER)
class LevyTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.ingress()
        self.ada = get_user_model().objects.create_user("ada", password="pw")
        self.pools = services.pools()

    def ingress(self):
        call_command("ingress_mana", stdout=StringIO(), stderr=StringIO())

    def held(self, role):
        pool = self.pools[role]
        return Decimal(services.balance_base_units(self.ada, pool)) / 10 ** 9


class ArmingTests(LevyTestCase):
    def test_both_levies_arrive_clamped_and_armed(self):
        for code in ("storage.gb_day", "security.plain_gb_day"):
            with self.subTest(code=code):
                rule = TaxRule.objects.get(metric_code=code)
                self.assertTrue(rule.clamp_to_balance)
                self.assertTrue(rule.active)
                self.assertIn("mana_armed_at", rule.metadata)

    def test_they_are_priced_in_their_pools(self):
        item = TariffItem.objects.get(metric__code="security.plain_gb_day")
        self.assertEqual(item.charged_asset.unit_name, "BLUE")
        item = TariffItem.objects.get(metric__code="storage.gb_day")
        self.assertEqual(item.charged_asset.unit_name, "GREEN")

    def test_a_staff_disarm_survives_the_next_deploy(self):
        from toto.quota import levies

        levies.set_armed("security.plain_gb_day", False)
        self.ingress()
        self.assertFalse(TaxRule.objects.get(metric_code="security.plain_gb_day").active)


class DrainTests(LevyTestCase):
    def levy(self, code):
        return tax.levy_rule(TaxRule.objects.get(metric_code=code))

    def test_plaintext_drains_security_mana_and_only_that(self):
        make_vault_file(self.ada, 2 * GB)          # 2 GB × 20 = 40 a day
        self.levy("security.plain_gb_day")
        self.assertEqual(self.held("security"), Decimal("60"))
        self.assertEqual(self.held("compute"), Decimal("100"))

    def test_an_empty_pool_is_clamped_and_freezes_nothing(self):
        from toto.quota import levies

        make_vault_file(self.ada, 10 * GB)         # 200 a day against 100 held
        summary = self.levy("security.plain_gb_day")
        self.assertEqual(summary.counts, {tax.Outcome.CLAMPED: 1})
        self.assertEqual(self.held("security"), Decimal("0"))
        self.assertFalse(TaxArrearsCase.objects.exists())
        self.assertFalse(levies.user_is_frozen(self.ada))

    def test_an_encrypted_file_drains_nothing(self):
        from toto.vault.models import VaultFile

        f = make_vault_file(self.ada, 2 * GB)
        VaultFile.objects.filter(pk=f.pk).update(is_encrypted=True)
        summary = self.levy("security.plain_gb_day")
        # Not even visited: the sample holds only members with plaintext.
        self.assertNotIn(tax.Outcome.LEVIED, summary.counts)
        self.assertNotIn(tax.Outcome.CLAMPED, summary.counts)
        self.assertEqual(self.held("security"), Decimal("100"))
