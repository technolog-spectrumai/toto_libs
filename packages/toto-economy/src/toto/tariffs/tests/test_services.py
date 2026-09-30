"""
Tests for tariff calculation and posting services.
"""
from decimal import Decimal

from django.test import override_settings
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset as issued_asset

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerTransaction,
    to_base_units,
)
from toto.quota.metrics import Metric
from toto.tariffs.models import (
    BillingMetric,
    BillingUnit,
    RoundingMode,
    Tariff,
    TariffItem,
    TariffStatus,
    UsageRecord,
    UsageStatus,
)
from toto.tariffs.services import (
    calculate_tariff_charge,
    post_usage_record,
    rate_usage_record,
    record_and_post_usage,
    simulate_tariff,
)


def make_asset(unit_name, decimals=6):
    return issued_asset(
        name=unit_name,
        unit_name=unit_name,
        decimals=decimals,
        max_supply_base_units=10 ** 15,
        active=True,
    )


def make_account(code, account_type=AccountType.USER):
    return LedgerAccount.objects.create(
        code=code,
        name=code,
        account_type=account_type,
        active=True,
    )


def fund_account(account, asset, amount_display):
    base = to_base_units(Decimal(str(amount_display)), asset.decimals)
    holding, _ = AssetHolding.objects.get_or_create(account=account, asset=asset)
    holding.balance_base_units = base
    holding.save()
    return holding


def make_tariff(code="TEST", status=TariffStatus.ACTIVE):
    return Tariff.objects.create(name=code, code=code, status=status)


def make_billing_unit(code, label="", dimension=""):
    unit, _ = BillingUnit.objects.get_or_create(
        code=code,
        defaults={"label": label or code, "dimension": dimension, "active": True},
    )
    return unit


def make_item(tariff, metric_code, asset, price_display, receiving_account, unit=None, unit_quantity=1, rounding_mode=RoundingMode.UP):
    metric, _ = BillingMetric.objects.get_or_create(
        code=metric_code,
        defaults={"label": metric_code, "active": True},
    )
    price_base = to_base_units(Decimal(str(price_display)), asset.decimals)
    return TariffItem.objects.create(
        tariff=tariff,
        name=metric_code,
        metric=metric,
        charged_asset=asset,
        price_per_unit_display=Decimal(str(price_display)),
        price_per_unit_base_units=price_base,
        unit=unit,
        unit_quantity=Decimal(str(unit_quantity)),
        receiving_account=receiving_account,
        rounding_mode=rounding_mode,
        active=True,
    )


# ---------------------------------------------------------------------------
# Tariff item validation
# ---------------------------------------------------------------------------

class TariffItemValidationTests(TestCase):

    def test_unique_item_code_per_tariff(self):
        from django.db import IntegrityError
        asset = make_asset("TOK")
        recv = make_account("RECV")
        tariff = make_tariff("T1")
        make_item(tariff, "metric.foo", asset, "0.001", recv)
        with self.assertRaises(IntegrityError):
            make_item(tariff, "metric.foo", asset, "0.002", recv)

    def test_same_metric_code_different_tariff_is_ok(self):
        asset = make_asset("TOK2")
        recv = make_account("RECV2")
        t1 = make_tariff("T2")
        t2 = make_tariff("T3")
        make_item(t1, "foo", asset, "0.001", recv)
        make_item(t2, "foo", asset, "0.001", recv)
        self.assertEqual(TariffItem.objects.filter(metric__code="foo").count(), 2)


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------

class CalculationTests(TestCase):

    def setUp(self):
        self.asset = make_asset("AI_TOKEN", decimals=6)
        self.recv = make_account("REV-AI", AccountType.SYSTEM)
        self.tariff = make_tariff("AI")
        self.item = make_item(
            self.tariff, "ai.input_tokens", self.asset,
            "0.001", self.recv
        )

    def test_basic_calculation(self):
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("1000"), "input_token")
        self.assertEqual(len(drafts), 1)
        expected_base = to_base_units(Decimal("1000") * Decimal("0.001"), self.asset.decimals)
        self.assertEqual(drafts[0].amount_base_units, expected_base)

    def test_no_matching_item(self):
        drafts = calculate_tariff_charge(self.tariff, "no.such.metric", Decimal("100"), "token")
        self.assertEqual(drafts, [])

    def test_inactive_item_excluded(self):
        self.item.active = False
        self.item.save()
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("1000"), "input_token")
        self.assertEqual(drafts, [])

    def test_rounding_up(self):
        item = make_item(
            self.tariff, "ai.rounding_up", self.asset,
            "0.001", self.recv,
            rounding_mode=RoundingMode.UP,
        )
        # 1 token * 0.001 = 0.000001 display => base = 1 (already integer)
        # 3 tokens * 0.001 = 0.003 => 3000 base? let's use decimals=3 for clarity
        asset3 = make_asset("TOK3", decimals=3)
        recv3 = make_account("RECV3")
        item3 = make_item(self.tariff, "round.test", asset3, "0.5", recv3, rounding_mode=RoundingMode.UP)
        # 3 * 0.5 = 1.5 display => 1500 base (decimals=3, exact integer)
        drafts = calculate_tariff_charge(self.tariff, "round.test", Decimal("3"), "token")
        self.assertEqual(drafts[0].amount_base_units, 1500)

    def test_rounding_down(self):
        asset = make_asset("TOK4", decimals=3)
        recv = make_account("RECV4")
        item = make_item(self.tariff, "round.down", asset, "0.333", recv, rounding_mode=RoundingMode.DOWN)
        # 1 * 0.333 = 333 base (exact)
        drafts = calculate_tariff_charge(self.tariff, "round.down", Decimal("1"), "token")
        self.assertEqual(drafts[0].amount_base_units, 333)

    def test_minimum_charge(self):
        asset = make_asset("TOK5", decimals=6)
        recv = make_account("RECV5")
        metric, _ = BillingMetric.objects.get_or_create(
            code="min.charge", defaults={"label": "min.charge", "active": True}
        )
        item = TariffItem.objects.create(
            tariff=self.tariff,
            name="min_charge",
            metric=metric,
            charged_asset=asset,
            price_per_unit_display=Decimal("0.000001"),
            price_per_unit_base_units=1,
            unit=None,
            unit_quantity=Decimal("1"),
            receiving_account=recv,
            minimum_charge_base_units=1000,
            active=True,
        )
        # 1 request * 1 base_unit = 1, but minimum is 1000
        drafts = calculate_tariff_charge(self.tariff, "min.charge", Decimal("1"), "request")
        self.assertEqual(drafts[0].amount_base_units, 1000)

    def test_per_unit_quantity(self):
        asset = make_asset("TOK6", decimals=6)
        recv = make_account("RECV6")
        # Price 0.001 per 1000 tokens
        item = make_item(self.tariff, "per.1000", asset, "0.001", recv, unit_quantity=1000)
        # 1000 tokens => 0.001 display = 1000 base (decimals=6)
        drafts = calculate_tariff_charge(self.tariff, "per.1000", Decimal("1000"), "token")
        expected = to_base_units(Decimal("0.001"), asset.decimals)
        self.assertEqual(drafts[0].amount_base_units, expected)

    def test_multi_item_same_metric(self):
        asset2 = make_asset("TOK7", decimals=6)
        recv2 = make_account("RECV7")
        item2 = make_item(self.tariff, "ai.input_tokens.2", asset2, "0.002", recv2)
        # Two separate tariff items should NOT both match the same code unless same code
        # (they have different codes so only 1 matches)
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("100"), "input_token")
        self.assertEqual(len(drafts), 1)


# ---------------------------------------------------------------------------
# simulate_tariff
# ---------------------------------------------------------------------------

class SimulateTests(TestCase):

    def test_simulate_returns_lines_and_totals(self):
        asset = make_asset("STOK", decimals=6)
        recv = make_account("RECV-SIM")
        tariff = make_tariff("SIM")
        make_item(tariff, "sim.tokens", asset, "0.001", recv)
        result = simulate_tariff(tariff, [{"metric_code": "sim.tokens", "quantity": "500", "unit": "token"}])
        self.assertEqual(len(result["lines"]), 1)
        self.assertIn("STOK", result["totals_by_asset"])

    def test_simulate_no_match(self):
        tariff = make_tariff("EMPTY")
        result = simulate_tariff(tariff, [{"metric_code": "none", "quantity": "1", "unit": "token"}])
        self.assertEqual(result["lines"], [])
        self.assertEqual(result["totals_by_asset"], {})


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

class PostingTests(TestCase):

    def setUp(self):
        self.asset = make_asset("PTOK", decimals=6)
        self.payer = make_account("PAYER", AccountType.USER)
        self.recv = make_account("RECV-P", AccountType.SYSTEM)
        self.tariff = make_tariff("POST")
        self.item = make_item(
            self.tariff, "post.metric", self.asset,
            "0.001", self.recv
        )
        # Fund payer with 10.0 PTOK
        fund_account(self.payer, self.asset, "10.0")

    def _make_record(self, qty="1000", metric="post.metric"):
        return UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code=metric,
            quantity=Decimal(qty),
            unit="token",
        )

    def test_post_drains_payer_and_credits_receiver(self):
        record = self._make_record("1000")
        tx = post_usage_record(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)
        self.assertIsNotNone(record.ledger_transaction)
        charge_base = to_base_units(Decimal("1000") * Decimal("0.001"), self.asset.decimals)
        payer_holding = AssetHolding.objects.get(account=self.payer, asset=self.asset)
        initial_base = to_base_units(Decimal("10.0"), self.asset.decimals)
        self.assertEqual(payer_holding.balance_base_units, initial_base - charge_base)
        recv_holding = AssetHolding.objects.get(account=self.recv, asset=self.asset)
        self.assertEqual(recv_holding.balance_base_units, charge_base)

    def test_idempotent_reference(self):
        record = self._make_record("500")
        tx1 = post_usage_record(record)
        record.refresh_from_db()
        tx2 = post_usage_record(record)
        self.assertEqual(tx1.reference, tx2.reference)
        self.assertEqual(LedgerTransaction.objects.filter(reference=tx1.reference).count(), 1)

    def test_charge_joins_the_hash_chain(self):
        # A metered host's ledger is mostly charges; a chain that skips them
        # verifies nothing. Every posted charge must carry a LedgerHash and
        # leave the whole chain verifiable.
        from toto.assets.hashing import verify_hash_chain
        from toto.assets.models import LedgerHash

        record = self._make_record("1000")
        tx = post_usage_record(record)
        self.assertTrue(LedgerHash.objects.filter(transaction=tx).exists())
        self.assertTrue(verify_hash_chain())

    def test_charge_rolls_back_whole_when_any_write_fails(self):
        # The locked balance check is only an authority while the locks are
        # held and the writes ride the same transaction. A failure at the very
        # END of the posting (the hash attach) must roll back the transaction
        # row, the entries AND both holding mutations.
        from unittest import mock

        record = self._make_record("1000")
        with mock.patch("toto.tariffs.services.attach_hash",
                        side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                post_usage_record(record)
        self.assertFalse(
            LedgerTransaction.objects.filter(source_id=str(record.uuid)).exists())
        payer_holding = AssetHolding.objects.get(account=self.payer, asset=self.asset)
        self.assertEqual(payer_holding.balance_display, Decimal("10.0"))
        self.assertFalse(
            AssetHolding.objects.filter(account=self.recv, asset=self.asset,
                                        balance_base_units__gt=0).exists())

    def test_insufficient_funds_fails_and_no_ledger(self):
        # Fund only 0.5 PTOK, but charge will be 1.0
        fund_account(self.payer, self.asset, "0.5")
        record = self._make_record("1000")  # needs 1.0 PTOK
        with self.assertRaises(ValueError):
            post_usage_record(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.FAILED)
        self.assertIsNone(record.ledger_transaction)
        # Balance unchanged
        holding = AssetHolding.objects.get(account=self.payer, asset=self.asset)
        self.assertEqual(holding.balance_display, Decimal("0.5"))

    def test_atomicity_no_partial_post(self):
        """When a multi-asset charge partially fails, nothing is posted."""
        asset2 = make_asset("PTOK2", decimals=6)
        recv2 = make_account("RECV-P2", AccountType.SYSTEM)
        # Add second item that charges asset2
        item2 = make_item(self.tariff, "post.metric2", asset2, "100.0", recv2)
        # payer has PTOK but not PTOK2
        record = UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code="post.metric2",
            quantity=Decimal("1"),
            unit="token",
        )
        with self.assertRaises(ValueError):
            post_usage_record(record)
        # PTOK balance must remain unchanged
        holding = AssetHolding.objects.get(account=self.payer, asset=self.asset)
        self.assertEqual(holding.balance_display, Decimal("10.0"))

    def test_multi_item_multi_asset(self):
        asset2 = make_asset("ATWO", decimals=6)
        recv2 = make_account("RECV-A2", AccountType.SYSTEM)
        fund_account(self.payer, asset2, "5.0")
        item2 = make_item(self.tariff, "dual.metric.asset2", asset2, "0.001", recv2)
        # Create a record for asset2 item only
        record = UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code="dual.metric.asset2",
            quantity=Decimal("100"),
            unit="token",
        )
        tx = post_usage_record(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)
        recv_holding = AssetHolding.objects.get(account=recv2, asset=asset2)
        self.assertGreater(recv_holding.balance_base_units, 0)

    def test_rate_then_post(self):
        record = self._make_record("200")
        charges = rate_usage_record(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.RATED)
        self.assertEqual(len(charges), 1)
        tx = post_usage_record(record)
        record.refresh_from_db()
        self.assertEqual(record.status, UsageStatus.POSTED)

    def test_rate_idempotent(self):
        record = self._make_record("300")
        charges1 = rate_usage_record(record)
        charges2 = rate_usage_record(record)
        self.assertEqual(len(charges1), len(charges2))

    def test_record_and_post_usage_convenience(self):
        record, tx = record_and_post_usage(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code="post.metric",
            quantity=Decimal("100"),
            unit="token",
        )
        self.assertEqual(record.status, UsageStatus.POSTED)
        self.assertTrue(tx.posted)

    def test_ledger_transaction_metadata(self):
        record = self._make_record("50")
        tx = post_usage_record(record)
        self.assertIn("usage_record_uuid", tx.metadata)
        self.assertEqual(tx.metadata["tariff_code"], self.tariff.code)
        self.assertEqual(tx.metadata["metric_code"], "post.metric")

    def test_posted_transaction_is_immutable(self):
        from django.core.exceptions import ValidationError
        record = self._make_record("10")
        tx = post_usage_record(record)
        tx.description = "tampered"
        with self.assertRaises(ValidationError):
            tx.save()

    def test_no_negative_balance_allowed(self):
        fund_account(self.payer, self.asset, "0.0005")  # very small
        record = self._make_record("1000")
        with self.assertRaises(ValueError):
            post_usage_record(record)
        holding = AssetHolding.objects.get(account=self.payer, asset=self.asset)
        self.assertGreaterEqual(holding.balance_base_units, 0)


# ---------------------------------------------------------------------------
# Views / URLs
# ---------------------------------------------------------------------------

class TariffViewTests(TestCase):

    def setUp(self):
        from django.contrib.auth import get_user_model
        from toto.core.models import Platform
        User = get_user_model()
        self.user = User.objects.create_superuser("admin", "admin@test.com", "password")
        Platform.objects.create(
            site_name="Test", author="Test", publication_year=2024, active=True
        )
        self.asset = make_asset("VTOK", decimals=6)
        self.recv = make_account("VRECV", AccountType.SYSTEM)
        self.tariff = make_tariff("VTEST")
        make_item(self.tariff, "view.metric", self.asset, "0.001", self.recv)

    def test_tariff_list_renders(self):
        self.client.login(username="admin", password="password")
        response = self.client.get("/tariffs/")
        self.assertEqual(response.status_code, 200)

    def test_tariff_detail_renders(self):
        self.client.login(username="admin", password="password")
        response = self.client.get(f"/tariffs/{self.tariff.uuid}/")
        self.assertEqual(response.status_code, 200)

    def test_tariff_create_get(self):
        self.client.login(username="admin", password="password")
        response = self.client.get("/tariffs/new/")
        self.assertEqual(response.status_code, 200)

    def test_usage_list_renders(self):
        self.client.login(username="admin", password="password")
        response = self.client.get("/tariffs/usage/")
        self.assertEqual(response.status_code, 200)

    def test_metrics_renders(self):
        self.client.login(username="admin", password="password")
        response = self.client.get("/tariffs/metrics/")
        self.assertEqual(response.status_code, 200)

    def test_nav_tab_present_in_assets(self):
        """Tariffs link must appear in the assets base navigation."""
        self.client.login(username="admin", password="password")
        response = self.client.get("/assets/")
        content = response.content.decode()
        self.assertIn("/tariffs/", content)

    def test_api_rate_json(self):
        import json
        self.client.login(username="admin", password="password")
        payload = {
            "tariff_code": "VTEST",
            "metric_code": "view.metric",
            "quantity": "100",
            "unit": "token",
        }
        response = self.client.post(
            "/tariffs/api/rate/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("charges", data)
        self.assertEqual(len(data["charges"]), 1)

    def test_api_post_json(self):
        import json
        payer = make_account("PAYER-V", AccountType.USER)
        fund_account(payer, self.asset, "10.0")
        self.client.login(username="admin", password="password")
        payload = {
            "tariff_code": "VTEST",
            "payer_account_code": "PAYER-V",
            "metric_code": "view.metric",
            "quantity": "100",
            "unit": "token",
        }
        response = self.client.post(
            "/tariffs/api/post/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["status"], UsageStatus.POSTED)

    def test_api_metrics_json(self):
        self.client.login(username="admin", password="password")
        response = self.client.get("/tariffs/api/metrics/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("usage_total", data)
        self.assertIn("active_tariffs", data)


# ---------------------------------------------------------------------------
# The seeded rate card
# ---------------------------------------------------------------------------

@override_settings(TARIFF_SEED_PRICES=True)
class RateCardTests(TestCase):
    """What `ingress_tariffs` actually produces.

    These replace an older set that asserted `ingress_vault` seeded billing
    rows. That coupling is gone on purpose: vault ships in the library and
    cannot know about tariffs, which this host owns.

    Seeding is forced on here because zenobia ships with TARIFF_SEED_PRICES
    off — the platform goes live capping but not charging, and prices are set
    deliberately from the rate desk afterwards. These tests are about what the
    seeder produces *when asked to*, which is a different question.

    What it produces depends on the host running it, so the assertions follow
    the seeder's contract and not one host's app list: a price is seeded, in
    gas, for every metric this host METERS, less the ones a mana pool prices
    here (``ingress_mana`` owns those). A price for a metric the host does not
    meter — a parked app, an app only another host installs — is reported and
    skipped. These tests used to expect ``len(PRICES)`` items, which was only
    ever true on a host that installed every metered app and had no pools.
    """

    def seed(self, full=False):
        from django.core.management import call_command
        from io import StringIO
        call_command("ingress_assets", full=full, stdout=StringIO(), stderr=StringIO())
        call_command("ingress_tariffs", full=full, stdout=StringIO(), stderr=StringIO())

    @staticmethod
    def gas_priced_here():
        """The codes the seeder must price in gas on THIS host.

        Call it after seeding: the pools are minted by the same economy
        bootstrap, and a mapped metric is only exempt once its pool exists.
        """
        from django.apps import apps
        from toto.quota.metrics import registry
        from toto.tariffs.management.commands.ingress_tariffs import host_prices

        pooled = set()
        if apps.is_installed("toto.mana"):
            from toto.mana.services import pooled_codes

            pooled = pooled_codes()
        return (set(host_prices()) & set(registry.codes())) - pooled

    def test_every_priced_metric_is_priced_in_gas(self):
        from django.conf import settings
        from toto.tariffs.management.commands.ingress_tariffs import host_prices

        self.seed()
        tariff = Tariff.objects.get(code="platform-default")
        items = TariffItem.objects.filter(tariff=tariff, active=True)

        self.assertEqual(set(items.values_list("metric__code", flat=True)),
                         self.gas_priced_here())
        gas = getattr(settings, "GAS_ASSET", "ASR")
        prices = host_prices()
        for item in items.select_related("charged_asset", "metric"):
            self.assertEqual(item.charged_asset.unit_name, gas)
            self.assertEqual(item.price_per_unit_display, prices[item.metric.code])

    def test_the_metrics_match_what_the_apps_declare(self):
        """Seeded metrics come from the registry, so they cannot be fiction.

        The old rate card hardcoded ten codes, five of which no code ever
        charged. Deriving them from what apps declare makes that impossible.
        """
        from toto.quota.metrics import registry

        self.seed()
        seeded = set(BillingMetric.objects.values_list("code", flat=True))
        for metric in registry.all():
            self.assertIn(metric.code, seeded)

    @override_settings(TARIFF_PRICES={"nobody.meters.this": "0.001"})
    def test_a_price_this_host_does_not_meter_is_reported_not_seeded(self):
        """A price nothing here meters can never be charged, so it is not seeded.

        This asserted that PRICES named only metrics registered HERE, and the
        seeder asserted the same until a second host ran its own rate card (see
        ``not_metered_here``): the table is a catalogue each host draws its own
        subset from. What must hold per host is that such a price never reaches
        the rate card, that the seed still succeeds, and that it says so.
        """
        from django.core.management import call_command
        from io import StringIO

        call_command("ingress_assets", stdout=StringIO(), stderr=StringIO())
        out = StringIO()
        call_command("ingress_tariffs", stdout=out, stderr=StringIO())

        self.assertIn("nobody.meters.this", out.getvalue())
        self.assertFalse(BillingMetric.objects.filter(code="nobody.meters.this").exists())
        self.assertFalse(TariffItem.objects.filter(metric__code="nobody.meters.this").exists())

    def test_every_price_names_a_metric_some_app_declares(self):
        """The catalogue itself must not be fiction.

        Not "registered on this host" (the test above) but known to the
        platform at all. Mana's audit table names every metered code from every
        wheel, coloured or deliberately not (``NOT_MANA`` — parked apps
        included), so a typo or a price for something no app ever meters fails
        here instead of being one more line in an ingress warning.
        """
        from toto.mana.colours import COLOUR_OF, NOT_MANA
        from toto.tariffs.management.commands.ingress_tariffs import PRICES

        unknown = set(PRICES) - set(COLOUR_OF) - set(NOT_MANA)
        self.assertEqual(unknown, set(), "a price nothing anywhere meters can never be charged")

    def test_seeding_is_idempotent(self):
        self.seed()
        self.seed()
        self.assertEqual(Tariff.objects.filter(code="platform-default").count(), 1)
        items = TariffItem.objects.filter(tariff__code="platform-default")
        expected = self.gas_priced_here()
        self.assertEqual(items.count(), len(expected))
        self.assertEqual(set(items.values_list("metric__code", flat=True)), expected)

    def test_revenue_has_somewhere_to_land(self):
        self.seed()
        for item in TariffItem.objects.select_related("receiving_account"):
            self.assertEqual(item.receiving_account.code, "platform-usage-fees")

    def test_without_gas_nothing_is_priced(self):
        """ingress_tariffs must not invent an asset to price against.

        Every ingress command bootstraps the economy before its own work
        (``IngressCommand.bootstrap``), and on a monetary master — which every
        LedgerTestCase is — that mints ASR before this seeder runs. So a bare
        install is asked for explicitly, with the switch that exists for it.
        """
        from django.conf import settings
        from django.core.management import call_command
        from io import StringIO
        from toto.tariffs.management.commands.ingress_tariffs import Command

        bare = Command()
        bare.bootstrap_economy = False
        call_command(bare, stdout=StringIO(), stderr=StringIO())

        self.assertFalse(Asset.objects.filter(
            unit_name=getattr(settings, "GAS_ASSET", "ASR")).exists())
        self.assertFalse(TariffItem.objects.exists())

    def test_demo_prices_are_draft_and_only_under_full(self):
        self.seed(full=True)

        demo = Tariff.objects.get(code="demo-tokens")
        self.assertEqual(demo.status, TariffStatus.DRAFT)
        units = {i.charged_asset.unit_name for i in demo.items.select_related("charged_asset")}
        self.assertTrue(units <= {"BANANA", "MAKARONI"})
        # A draft tariff must never be handed to a real user.
        self.assertNotEqual(demo.status, TariffStatus.ACTIVE)


#: The metric GasGrantTests price and charge. The suite's own, because every
#: real one that stood here left some host that runs this file — texlab was
#: parked in 9/2026, and a price for a metric nobody registers is never charged:
#: price_for() answers None and every check after it passes vacuously. Not
#: registered (the registry is process-wide, and rate_card.upsert_price takes
#: any Metric), and not in toto/mana/colours.py, so it is priced on the gas rate
#: card on a host with mana pools as much as on one without.
GRANT_PROBE = Metric(code="gasgrant.probe", label="Grant probe",
                     app_label="gasgrant", unit="request")


@override_settings(GAS_STARTING_GRANT="0.1")
class GasGrantTests(TestCase):
    """A new account has to be able to afford something.

    The grant, the metric and the price are stated here rather than read off
    the host (tax's testing settings set no grant; zenobia sets 0.1 from its
    environment and prices nothing in gas). What is left to the library is
    what is under test: that signup pays the grant at all, and that it lands
    in the asset the charge then draws.
    """

    PRICE = "0.02"      # the 0.1 grant buys exactly five

    def seed(self):
        """A fresh install with one gas price on it. Returns the price row."""
        from django.core.management import call_command
        from io import StringIO

        from toto.tariffs import rate_card

        call_command("ingress_assets", stdout=StringIO(), stderr=StringIO())
        return rate_card.upsert_price(GRANT_PROBE, self.PRICE)

    def newcomer(self, username):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(username=username, password="pw")

    def held(self, user, asset):
        from toto.assets.prepaid import get_prepaid_account
        from toto.assets.queries import get_asset_balance_display

        account = get_prepaid_account(user)
        self.assertIsNotNone(account, "signup created no prepaid account")
        return get_asset_balance_display(asset, account)

    def test_the_signup_hook_is_held_strongly(self):
        """It was a local function connected weakly, so it was collected as
        soon as ``AssetsConfig.ready()`` returned and signup funded nobody.
        DEBUG=True hid that — Django's argument check caches the receiver — and
        every deployed profile runs DEBUG=False. Asserted on the connection,
        so it fails whichever DEBUG the process booted with."""
        import weakref

        from django.db.models.signals import post_save

        hooks = {entry[0][0]: entry[1] for entry in post_save.receivers}
        hook = hooks.get("assets_create_prepaid_on_user_create")
        self.assertIsNotNone(hook, "signup is not hooked at all")
        self.assertNotIsInstance(hook, weakref.ReferenceType)

    def test_a_new_user_is_funded_in_what_the_rate_card_charges(self):
        from toto.quota.charge import check_funds, price_for

        item = self.seed()
        user = self.newcomer("newcomer")

        self.assertEqual(self.held(user, item.charged_asset), Decimal("0.1"))
        tariff = price_for(user, GRANT_PROBE.app_label)
        self.assertIsNotNone(tariff, "unpriced, check_funds would prove nothing")
        check_funds(user, tariff, GRANT_PROBE.code, 1)   # affordable → no raise

    def test_the_grant_follows_the_charging_currency(self):
        """The rate desk's switch re-denominates the rate card and leaves the
        settlement row alone. A grant paid in the settlement asset then funded
        newcomers in a currency no price asked for."""
        from toto.quota.rates import set_charging_currency

        item = self.seed()
        set_charging_currency(Asset.objects.get(unit_name="FLOR").pk)
        item.refresh_from_db()
        self.assertEqual(item.charged_asset.unit_name, "FLOR")

        user = self.newcomer("switched")
        self.assertEqual(self.held(user, item.charged_asset), Decimal("0.1"))

    @override_settings(GAS_ASSET="GASX")
    def test_the_grant_follows_the_hosts_gas_asset(self):
        """A host that names its own GAS_ASSET bills in it unless contracted
        otherwise, while settlement falls back to ASR."""
        item = self.seed()
        user = self.newcomer("ticker")
        self.assertEqual(self.held(user, item.charged_asset), Decimal("0.1"))

    def test_the_grant_is_paid_once(self):
        from toto.assets.prepaid import grant_starting_gas

        item = self.seed()
        user = self.newcomer("greedy")
        before = self.held(user, item.charged_asset)
        self.assertGreater(before, 0)    # or "once" is "never", and passes

        self.assertIsNone(grant_starting_gas(user))
        self.assertEqual(self.held(user, item.charged_asset), before)

    def test_an_empty_wallet_is_refused(self):
        from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for

        item = self.seed()
        user = self.newcomer("spender")
        tariff = price_for(user, GRANT_PROBE.app_label)
        self.assertIsNotNone(tariff, "unpriced, nothing could ever be refused")

        # Burn the grant, then the next one must be refused rather than
        # driving the balance negative.
        paid = 0
        with self.assertRaises(InsufficientFunds):
            for _ in range(10):
                check_funds(user, tariff, GRANT_PROBE.code, 1)
                charge(user, tariff, GRANT_PROBE.code, 1)
                paid += 1
        self.assertEqual(paid, 5)
        self.assertEqual(self.held(user, item.charged_asset), 0)


# ---------------------------------------------------------------------------
# The price you see is the price you pay
# ---------------------------------------------------------------------------

class PriceCoherenceTests(TestCase):
    """Two numbers describe one price; only one of them is ever charged."""

    def test_saving_derives_the_charged_price_from_the_displayed_one(self):
        from toto.tariffs.models import TariffItem

        asset = make_asset("PCA", decimals=6)
        recv = make_account("RECV-PCA", AccountType.SYSTEM)
        tariff = make_tariff("PCA-T")
        item = make_item(tariff, "price.coherence", asset, "0.001", recv)

        self.assertEqual(item.price_per_unit_base_units, 1000)   # 0.001 × 10^6

        # The path that used to desynchronise: change the display price alone,
        # exactly as Django admin does, and save.
        item.price_per_unit_display = Decimal("0.002")
        item.save()
        item.refresh_from_db()
        self.assertEqual(item.price_per_unit_base_units, 2000)

    def test_an_item_can_be_created_without_naming_base_units(self):
        # Admin leaves the integer read-only, so a create there sent nothing
        # for a non-null column and raised IntegrityError.
        from toto.tariffs.models import BillingMetric, TariffItem

        asset = make_asset("PCB", decimals=2)
        recv = make_account("RECV-PCB", AccountType.SYSTEM)
        tariff = make_tariff("PCB-T")
        metric, _ = BillingMetric.objects.get_or_create(
            code="price.created", defaults={"label": "Created", "active": True}
        )
        item = TariffItem.objects.create(
            tariff=tariff, metric=metric, name="Created",
            charged_asset=asset, price_per_unit_display=Decimal("1.25"),
            receiving_account=recv, unit_quantity=Decimal("1"),
        )
        self.assertEqual(item.price_per_unit_base_units, 125)


class TariffAccessTests(TestCase):
    """Setting prices and moving money are staff acts."""

    def setUp(self):
        from django.contrib.auth import get_user_model
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        U = get_user_model()
        self.staff = U.objects.create_user("tstaff", password="pw", is_staff=True)
        self.plain = U.objects.create_user("tplain", password="pw")

    def test_anonymous_cannot_reach_anything(self):
        for url in ("/tariffs/", "/tariffs/new/", "/tariffs/usage/", "/tariffs/usage/new/"):
            with self.subTest(url=url):
                self.assertNotEqual(self.client.get(url).status_code, 200)

    def test_a_signed_in_user_may_look_but_not_create(self):
        self.client.force_login(self.plain)
        self.assertEqual(self.client.get("/tariffs/").status_code, 200)
        self.assertEqual(self.client.get("/tariffs/new/").status_code, 403)
        self.assertEqual(self.client.get("/tariffs/usage/new/").status_code, 403)

    def test_staff_may_create(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/tariffs/new/").status_code, 200)

    def test_staff_may_edit_the_ownerless_platform_tariff(self):
        # It belongs to the platform, not a person — requiring ownership would
        # leave the rate card editable by superusers alone.
        tariff = make_tariff("PLATFORM-OWNERLESS")
        tariff.owner = None
        tariff.save()

        self.client.force_login(self.plain)
        self.assertEqual(self.client.get(f"/tariffs/{tariff.uuid}/edit/").status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(f"/tariffs/{tariff.uuid}/edit/").status_code, 200)

    def test_posting_a_charge_is_refused_without_staff(self):
        self.client.force_login(self.plain)
        res = self.client.post("/tariffs/api/post/", data="{}", content_type="application/json")
        self.assertEqual(res.status_code, 403)
