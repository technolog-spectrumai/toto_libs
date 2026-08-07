"""Setting a price, from the seeder and from the staff grid.

The load-bearing property is that those two are the same code. A price typed
into the rate desk and a price seeded by ``ingress_tariffs`` must produce a
byte-identical TariffItem, or the two screens will eventually disagree about
what anybody pays.
"""
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.assets.models import AccountType, Asset, LedgerAccount, to_base_units
from toto.quota.metrics import Metric
from toto.tariffs import rate_card
from toto.tariffs.models import BillingMetric, BillingUnit, Tariff, TariffItem

GAS = "TESTGAS"

METRIC = Metric(
    code="demo.thing", label="A demo thing", app_label="vault",
    unit="request", default_limit=Decimal("10"),
)


@override_settings(GAS_ASSET=GAS)
class RateCardTests(TestCase):
    def setUp(self):
        reserve = LedgerAccount.objects.create(
            code=f"RES-{GAS}", name="Reserve",
            account_type=AccountType.RESERVE, active=True,
        )
        self.gas = Asset.objects.create(
            name="Test Gas", unit_name=GAS, decimals=9,
            total_supply_base_units=10 ** 15, active=True,
            reserve_account=reserve,
        )

    def test_a_price_typed_by_staff_derives_its_base_units(self):
        """The regression pin: only the derived integer is ever billed."""
        item = rate_card.upsert_price(METRIC, "0.00002")
        item.refresh_from_db()
        self.assertEqual(
            item.price_per_unit_base_units,
            to_base_units(Decimal("0.00002"), self.gas.decimals),
        )

    def test_repricing_moves_the_billed_number_too(self):
        rate_card.upsert_price(METRIC, "0.00002")
        item = rate_card.upsert_price(METRIC, "0.001")
        item.refresh_from_db()
        self.assertEqual(item.price_per_unit_display, Decimal("0.001"))
        self.assertEqual(
            item.price_per_unit_base_units,
            to_base_units(Decimal("0.001"), self.gas.decimals),
        )
        self.assertEqual(TariffItem.objects.count(), 1)

    def test_defaulting_creates_asset_account_metric_and_unit(self):
        """Staff type a number; everything else is found or made for them."""
        item = rate_card.upsert_price(METRIC, "0.5")

        self.assertEqual(item.charged_asset, self.gas)
        self.assertEqual(item.receiving_account.code, rate_card.REVENUE_ACCOUNT_CODE)
        self.assertEqual(item.tariff.code, rate_card.DEFAULT_TARIFF_CODE)
        self.assertIsNone(item.tariff.owner_id, "the platform card must stay ownerless")
        self.assertTrue(BillingMetric.objects.filter(code=METRIC.code).exists())
        self.assertTrue(BillingUnit.objects.filter(code=METRIC.unit).exists())

    def test_blanking_deletes_the_item_and_the_metric_goes_free(self):
        rate_card.upsert_price(METRIC, "0.5")
        self.assertTrue(rate_card.remove_price(METRIC.code))

        self.assertFalse(TariffItem.objects.filter(metric__code=METRIC.code).exists())
        # The mirrors survive — UsageCharge history PROTECTs them.
        self.assertTrue(BillingMetric.objects.filter(code=METRIC.code).exists())
        # And removing it twice is not an error.
        self.assertFalse(rate_card.remove_price(METRIC.code))

    def test_rate_card_carries_no_model_instances(self):
        """toto.quota renders these rows and must not be able to reach a model."""
        rate_card.upsert_price(METRIC, "0.25")
        row = rate_card.rate_card()[METRIC.code]

        self.assertEqual(row["price_display"], Decimal("0.25"))
        self.assertEqual(row["asset"], GAS)
        for value in row.values():
            self.assertNotIsInstance(value, (TariffItem, Asset, BillingMetric))

    def test_parse_price_distinguishes_blank_from_zero(self):
        self.assertIsNone(rate_card.parse_price(""))
        self.assertIsNone(rate_card.parse_price("   "))
        self.assertEqual(rate_card.parse_price("0"), Decimal("0"))
        with self.assertRaises(ValueError):
            rate_card.parse_price("free")
        with self.assertRaises(ValueError):
            rate_card.parse_price("-1")

    def test_pricing_without_a_gas_asset_refuses_loudly(self):
        Asset.objects.filter(unit_name=GAS).update(active=False)
        with self.assertRaises(rate_card.NoGasAsset):
            rate_card.upsert_price(METRIC, "1")


@override_settings(GAS_ASSET=GAS, TARIFF_SEED_PRICES=True)
class SeederAgreesWithTheGridTests(TestCase):
    """The one implementation, proven from both ends.

    Seeding is forced on: zenobia ships with TARIFF_SEED_PRICES off so the
    platform goes live capping but not charging. What the seeder writes *when
    asked to* is still the thing under test.
    """

    def setUp(self):
        reserve = LedgerAccount.objects.create(
            code=f"RES-{GAS}", name="Reserve",
            account_type=AccountType.RESERVE, active=True,
        )
        Asset.objects.create(
            name="Test Gas", unit_name=GAS, decimals=9,
            total_supply_base_units=10 ** 15, active=True,
            reserve_account=reserve,
        )

    FIELDS = ("name", "charged_asset_id", "price_per_unit_display",
              "price_per_unit_base_units", "unit_id", "unit_quantity",
              "receiving_account_id", "minimum_charge_base_units",
              "rounding_mode", "active")

    def test_the_seeder_and_the_grid_produce_identical_items(self):
        from toto.quota.metrics import registry

        metric = registry.get("storage.request")
        if metric is None:  # vault not installed on this host
            self.skipTest("storage.request is not registered here")

        call_command("ingress_tariffs", verbosity=0)
        seeded = TariffItem.objects.get(metric__code=metric.code)
        before = {f: getattr(seeded, f) for f in self.FIELDS}

        price = seeded.price_per_unit_display
        seeded.delete()
        typed = rate_card.upsert_price(metric, price)
        after = {f: getattr(typed, f) for f in self.FIELDS}

        self.assertEqual(before, after)

    @override_settings(TARIFF_SEED_PRICES=False)
    def test_seed_prices_off_produces_a_tariff_with_no_items(self):
        """How the economy goes live without charging anyone yet."""
        call_command("ingress_tariffs", verbosity=0)

        tariff = Tariff.objects.get(code=rate_card.DEFAULT_TARIFF_CODE)
        self.assertEqual(tariff.items.count(), 0)
        # The catalogue is still complete — the metrics are known, just unpriced.
        self.assertTrue(BillingMetric.objects.exists())


@override_settings(GAS_ASSET=GAS)
class PricingCurrencyTests(TestCase):
    """Which asset a price is denominated in, and who gets to decide.

    The chain is item → tariff default → host gas asset, and the last step is
    what keeps a host that has only ever billed in gas working unchanged after
    the tariff gained a currency of its own.
    """

    def setUp(self):
        reserve = LedgerAccount.objects.create(
            code=f"RES-{GAS}", name="Reserve",
            account_type=AccountType.RESERVE, active=True,
        )
        self.gas = Asset.objects.create(
            name="Test Gas", unit_name=GAS, decimals=9,
            total_supply_base_units=10 ** 15, active=True,
            reserve_account=reserve,
        )
        coin_reserve = LedgerAccount.objects.create(
            code="RES-COIN", name="Coin reserve",
            account_type=AccountType.RESERVE, active=True,
        )
        self.coin = Asset.objects.create(
            name="Side Coin", unit_name="COIN", decimals=2,
            total_supply_base_units=10 ** 9, active=True,
            reserve_account=coin_reserve,
        )

    def test_without_a_choice_a_price_lands_in_the_host_gas_asset(self):
        item = rate_card.upsert_price(METRIC, Decimal("1.5"))
        self.assertEqual(item.charged_asset, self.gas)

    def test_an_explicit_asset_prices_one_metric_on_its_own(self):
        item = rate_card.upsert_price(METRIC, Decimal("1.5"), asset=self.coin)
        self.assertEqual(item.charged_asset, self.coin)
        # And the derivation follows THAT asset's decimals, not gas's — the
        # whole point of a per-metric currency is that the integer changes too.
        self.assertEqual(item.price_per_unit_base_units,
                         to_base_units(Decimal("1.5"), self.coin.decimals))

    def test_the_tariff_default_applies_when_the_metric_names_none(self):
        tariff = rate_card.default_tariff()
        tariff.default_asset = self.coin
        tariff.save()
        item = rate_card.upsert_price(METRIC, Decimal("2"))
        self.assertEqual(item.charged_asset, self.coin)

    def test_an_explicit_asset_beats_the_tariff_default(self):
        tariff = rate_card.default_tariff()
        tariff.default_asset = self.coin
        tariff.save()
        item = rate_card.upsert_price(METRIC, Decimal("2"), asset=self.gas)
        self.assertEqual(item.charged_asset, self.gas)

    def test_pricing_asset_falls_back_rather_than_returning_nothing(self):
        tariff = rate_card.default_tariff()
        self.assertIsNone(tariff.default_asset)
        self.assertEqual(tariff.pricing_asset(), self.gas)

    def test_the_rate_card_row_carries_the_asset_id_for_a_picker(self):
        rate_card.upsert_price(METRIC, Decimal("1"), asset=self.coin)
        row = rate_card.rate_card()[METRIC.code]
        self.assertEqual(row["asset_id"], self.coin.pk)
        self.assertEqual(row["asset"], "COIN")

    def test_the_desk_resolves_its_currency_column_to_an_asset(self):
        """What the grid posts, and what that must mean.

        `set_price` itself needs a REGISTERED metric, so the part worth pinning
        is the resolution: "" (the "default" option) and a stale id both inherit
        rather than failing, and a real id selects that asset.
        """
        from toto.quota import rates

        self.assertIsNone(rates._asset_by_id(""))          # the "default" option
        self.assertIsNone(rates._asset_by_id(None))
        self.assertIsNone(rates._asset_by_id(999999))      # stale <option>
        self.assertEqual(rates._asset_by_id(self.coin.pk), self.coin)

    def test_an_inactive_asset_is_never_selectable(self):
        from toto.quota import rates

        self.coin.active = False
        self.coin.save()
        self.assertIsNone(rates._asset_by_id(self.coin.pk))
        self.assertNotIn("COIN", [a["symbol"] for a in rates.billing_assets()])

    def test_billing_assets_lists_what_the_picker_offers(self):
        from toto.quota import rates

        symbols = [a["symbol"] for a in rates.billing_assets()]
        self.assertIn(GAS, symbols)
        self.assertIn("COIN", symbols)

    def test_inheriting_and_overriding_produce_different_base_units(self):
        """The regression that matters: the derived integer follows the asset."""
        inherited = rate_card.upsert_price(METRIC, Decimal("1.5"))
        self.assertEqual(inherited.charged_asset, self.gas)
        overridden = rate_card.upsert_price(METRIC, Decimal("1.5"), asset=self.coin)
        self.assertEqual(overridden.charged_asset, self.coin)
        self.assertNotEqual(inherited.price_per_unit_base_units,
                            overridden.price_per_unit_base_units)
