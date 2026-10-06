"""Pricing in colour: seeded once, owned by staff, never re-denominated away."""

from decimal import Decimal
from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.models import Asset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import colours, services
from toto.tariffs.models import TariffItem

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)


@override_settings(**MASTER)
class PricingTestCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()

    def ingress(self, command="ingress_mana"):
        out = StringIO()
        call_command(command, stdout=out, stderr=StringIO())
        return out.getvalue()

    def item(self, code):
        return (TariffItem.objects.select_related("charged_asset", "receiving_account")
                .get(tariff__code="platform-default", metric__code=code))

    def priced(self, code):
        return TariffItem.objects.filter(tariff__code="platform-default",
                                         metric__code=code).exists()


class SeedTests(PricingTestCase):
    def test_each_metric_is_priced_in_its_pool(self):
        self.ingress()
        self.assertEqual(self.item("storage.request").charged_asset.unit_name, "GREEN")
        self.assertEqual(self.item("workflows.run").charged_asset.unit_name, "RED")

    def test_the_seed_price_and_the_revenue_account(self):
        self.ingress()
        item = self.item("storage.request")
        self.assertEqual(item.price_per_unit_display, colours.PRICES["storage.request"])
        self.assertEqual(item.receiving_account.code, "platform-usage-fees")

    def test_egress_stays_unpriced(self):
        self.ingress()
        self.assertFalse(self.priced("storage.egress_mb"))

    @override_settings(MANA_PRICES={"storage.request": "7", "repo.op": None})
    def test_a_host_overrides_one_and_frees_another(self):
        self.ingress()
        self.assertEqual(self.item("storage.request").price_per_unit_display, Decimal("7"))
        self.assertFalse(self.priced("repo.op"))

    GEOGRAPHY = ("geography.lookup", "geography.route", "geography.pin", "geography.zone",
                 "geography.note")

    def geography_or_skip(self):
        from django.apps import apps

        if not apps.is_installed("toto.geography"):
            self.skipTest("this host installs no geography")

    def test_geography_s_prices_reach_the_rate_card_by_themselves(self):
        """Stage 63 added no seeding of its own: its codes are in
        ``colours.PRICES`` and the one ingress prices them, each in its
        pool, on the rate card the staff price desk reads and edits."""
        self.geography_or_skip()
        self.ingress()
        for code in self.GEOGRAPHY:
            with self.subTest(code=code):
                item = self.item(code)
                self.assertEqual(item.price_per_unit_display, colours.PRICES[code])
                self.assertEqual(item.charged_asset.unit_name,
                                 colours.TICKER[colours.COLOUR_OF[code]])
                self.assertEqual(item.receiving_account.code, "platform-usage-fees")
        self.assertEqual(self.item("geography.route").charged_asset.unit_name, "RED")
        self.assertEqual(self.item("geography.pin").charged_asset.unit_name, "GREEN")

    @override_settings(MANA_PRICES={"geography.route": "3", "geography.note": None})
    def test_a_host_overrides_a_geography_price_and_frees_another(self):
        self.geography_or_skip()
        self.ingress()
        self.assertEqual(self.item("geography.route").price_per_unit_display, Decimal("3"))
        self.assertFalse(self.priced("geography.note"))
        self.assertEqual(self.item("geography.lookup").price_per_unit_display,
                         colours.PRICES["geography.lookup"])

    @override_settings(MANA_SEED_PRICES=False)
    def test_the_seed_can_be_switched_off(self):
        self.ingress()
        self.assertFalse(TariffItem.objects.filter(
            metric__code__in=list(colours.COLOUR_OF)).exists())


class OwnershipTests(PricingTestCase):
    def test_a_staff_number_survives_the_next_deploy(self):
        self.ingress()
        item = self.item("storage.request")
        item.price_per_unit_display = Decimal("9")
        item.save()
        self.ingress()
        self.assertEqual(self.item("storage.request").price_per_unit_display, Decimal("9"))

    def test_a_wrong_denomination_is_repaired_and_the_number_kept(self):
        self.ingress()
        item = self.item("workflows.run")
        item.charged_asset = Asset.objects.get(unit_name="ASR")
        item.price_per_unit_display = Decimal("3")
        item.save()
        self.ingress()
        item = self.item("workflows.run")
        self.assertEqual(item.charged_asset.unit_name, "RED")
        self.assertEqual(item.price_per_unit_display, Decimal("3"))
        self.assertEqual(item.price_per_unit_base_units, 3 * 10 ** 9)


class OtherPriceWritersTests(PricingTestCase):
    def test_the_gas_seeder_after_mana_neither_asserts_nor_steals_a_metric(self):
        self.ingress()
        with override_settings(TARIFF_SEED_PRICES=True):
            out = self.ingress("ingress_tariffs")
        self.assertIn("priced by ingress_mana", out)
        self.assertEqual(self.item("storage.request").charged_asset.unit_name, "GREEN")

    def test_the_rate_desk_keeps_a_mana_metric_in_its_pool(self):
        """Even after staff chose a different charging currency."""
        from toto.quota import rates
        from toto.tariffs.rate_card import default_tariff

        tariff = default_tariff()
        tariff.default_asset = Asset.objects.get(unit_name="FLOR")
        tariff.save(update_fields=["default_asset"])
        self.assertTrue(rates.set_price("workflows.run", "3"))
        self.assertEqual(self.item("workflows.run").charged_asset.unit_name, "RED")

    def test_the_charging_currency_switch_leaves_pools_alone(self):
        from toto.quota import rates

        self.ingress()
        self.assertTrue(rates.set_price("subscription.month", "1"))
        flor = Asset.objects.get(unit_name="FLOR")
        rates.set_charging_currency(flor.pk)
        self.assertEqual(self.item("subscription.month").charged_asset.unit_name, "FLOR")
        self.assertEqual(self.item("storage.request").charged_asset.unit_name, "GREEN")


class AuditTests(PricingTestCase):
    def test_an_uncoloured_metric_is_reported_as_running_free(self):
        with mock.patch.dict(colours.COLOUR_OF):
            del colours.COLOUR_OF["storage.request"]
            undecided, _ = services.audit()
            out = self.ingress()
        self.assertIn("storage.request", undecided)
        self.assertIn("run FREE", out)

    def test_a_complete_map_reports_nothing_undecided(self):
        self.assertEqual(services.audit()[0], [])


@override_settings(MONETARY_ISSUER_KEY="", ASSETS_MONETARY_MASTER=False)
class BranchTests(TestCase):
    def test_no_pools_means_nothing_priced_and_no_crash(self):
        out = StringIO()
        call_command("ingress_mana", stdout=out, stderr=StringIO())
        self.assertIn("no pools", out.getvalue())
