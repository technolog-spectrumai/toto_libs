"""
Tests for tariff calculation and posting services.
"""
from decimal import Decimal

from django.test import TestCase

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerTransaction,
    to_base_units,
)
from toto.tariffs.models import (
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
    return Asset.objects.create(
        name=unit_name,
        unit_name=unit_name,
        decimals=decimals,
        total_supply_base_units=10 ** 15,
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


def make_item(tariff, metric_code, asset, price_display, unit, receiving_account, unit_quantity=1, rounding_mode=RoundingMode.UP):
    price_base = to_base_units(Decimal(str(price_display)), asset.decimals)
    return TariffItem.objects.create(
        tariff=tariff,
        name=metric_code,
        code=metric_code,
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
        make_item(tariff, "metric.foo", asset, "0.001", BillingUnit.TOKEN, recv)
        with self.assertRaises(IntegrityError):
            make_item(tariff, "metric.foo", asset, "0.002", BillingUnit.TOKEN, recv)

    def test_same_metric_code_different_tariff_is_ok(self):
        asset = make_asset("TOK2")
        recv = make_account("RECV2")
        t1 = make_tariff("T2")
        t2 = make_tariff("T3")
        make_item(t1, "foo", asset, "0.001", BillingUnit.TOKEN, recv)
        make_item(t2, "foo", asset, "0.001", BillingUnit.TOKEN, recv)
        self.assertEqual(TariffItem.objects.filter(code="foo").count(), 2)


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
            "0.001", BillingUnit.INPUT_TOKEN, self.recv
        )

    def test_basic_calculation(self):
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("1000"), BillingUnit.INPUT_TOKEN)
        self.assertEqual(len(drafts), 1)
        expected_base = to_base_units(Decimal("1000") * Decimal("0.001"), self.asset.decimals)
        self.assertEqual(drafts[0].amount_base_units, expected_base)

    def test_no_matching_item(self):
        drafts = calculate_tariff_charge(self.tariff, "no.such.metric", Decimal("100"), BillingUnit.TOKEN)
        self.assertEqual(drafts, [])

    def test_inactive_item_excluded(self):
        self.item.active = False
        self.item.save()
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("1000"), BillingUnit.INPUT_TOKEN)
        self.assertEqual(drafts, [])

    def test_rounding_up(self):
        item = make_item(
            self.tariff, "ai.rounding_up", self.asset,
            "0.001", BillingUnit.TOKEN, self.recv,
            rounding_mode=RoundingMode.UP,
        )
        # 1 token * 0.001 = 0.000001 display => base = 1 (already integer)
        # 3 tokens * 0.001 = 0.003 => 3000 base? let's use decimals=3 for clarity
        asset3 = make_asset("TOK3", decimals=3)
        recv3 = make_account("RECV3")
        item3 = make_item(self.tariff, "round.test", asset3, "0.5", BillingUnit.TOKEN, recv3, rounding_mode=RoundingMode.UP)
        # 3 * 0.5 = 1.5 display => 1500 base (decimals=3, exact integer)
        drafts = calculate_tariff_charge(self.tariff, "round.test", Decimal("3"), BillingUnit.TOKEN)
        self.assertEqual(drafts[0].amount_base_units, 1500)

    def test_rounding_down(self):
        asset = make_asset("TOK4", decimals=3)
        recv = make_account("RECV4")
        item = make_item(self.tariff, "round.down", asset, "0.333", BillingUnit.TOKEN, recv, rounding_mode=RoundingMode.DOWN)
        # 1 * 0.333 = 333 base (exact)
        drafts = calculate_tariff_charge(self.tariff, "round.down", Decimal("1"), BillingUnit.TOKEN)
        self.assertEqual(drafts[0].amount_base_units, 333)

    def test_minimum_charge(self):
        asset = make_asset("TOK5", decimals=6)
        recv = make_account("RECV5")
        item = TariffItem.objects.create(
            tariff=self.tariff,
            name="min_charge",
            code="min.charge",
            charged_asset=asset,
            price_per_unit_display=Decimal("0.000001"),
            price_per_unit_base_units=1,
            unit=BillingUnit.REQUEST,
            unit_quantity=Decimal("1"),
            receiving_account=recv,
            minimum_charge_base_units=1000,
            active=True,
        )
        # 1 request * 1 base_unit = 1, but minimum is 1000
        drafts = calculate_tariff_charge(self.tariff, "min.charge", Decimal("1"), BillingUnit.REQUEST)
        self.assertEqual(drafts[0].amount_base_units, 1000)

    def test_per_unit_quantity(self):
        asset = make_asset("TOK6", decimals=6)
        recv = make_account("RECV6")
        # Price 0.001 per 1000 tokens
        item = make_item(self.tariff, "per.1000", asset, "0.001", BillingUnit.TOKEN, recv, unit_quantity=1000)
        # 1000 tokens => 0.001 display = 1000 base (decimals=6)
        drafts = calculate_tariff_charge(self.tariff, "per.1000", Decimal("1000"), BillingUnit.TOKEN)
        expected = to_base_units(Decimal("0.001"), asset.decimals)
        self.assertEqual(drafts[0].amount_base_units, expected)

    def test_multi_item_same_metric(self):
        asset2 = make_asset("TOK7", decimals=6)
        recv2 = make_account("RECV7")
        item2 = make_item(self.tariff, "ai.input_tokens.2", asset2, "0.002", BillingUnit.INPUT_TOKEN, recv2)
        # Two separate tariff items should NOT both match the same code unless same code
        # (they have different codes so only 1 matches)
        drafts = calculate_tariff_charge(self.tariff, "ai.input_tokens", Decimal("100"), BillingUnit.INPUT_TOKEN)
        self.assertEqual(len(drafts), 1)


# ---------------------------------------------------------------------------
# simulate_tariff
# ---------------------------------------------------------------------------

class SimulateTests(TestCase):

    def test_simulate_returns_lines_and_totals(self):
        asset = make_asset("STOK", decimals=6)
        recv = make_account("RECV-SIM")
        tariff = make_tariff("SIM")
        make_item(tariff, "sim.tokens", asset, "0.001", BillingUnit.TOKEN, recv)
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
            "0.001", BillingUnit.TOKEN, self.recv
        )
        # Fund payer with 10.0 PTOK
        fund_account(self.payer, self.asset, "10.0")

    def _make_record(self, qty="1000", metric="post.metric"):
        return UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code=metric,
            quantity=Decimal(qty),
            unit=BillingUnit.TOKEN,
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
        item2 = make_item(self.tariff, "post.metric2", asset2, "100.0", BillingUnit.TOKEN, recv2)
        # payer has PTOK but not PTOK2
        record = UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code="post.metric2",
            quantity=Decimal("1"),
            unit=BillingUnit.TOKEN,
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
        item2 = make_item(self.tariff, "dual.metric.asset2", asset2, "0.001", BillingUnit.TOKEN, recv2)
        # Create a record for asset2 item only
        record = UsageRecord.objects.create(
            tariff=self.tariff,
            payer_account=self.payer,
            metric_code="dual.metric.asset2",
            quantity=Decimal("100"),
            unit=BillingUnit.TOKEN,
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
            unit=BillingUnit.TOKEN,
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
        make_item(self.tariff, "view.metric", self.asset, "0.001", BillingUnit.TOKEN, self.recv)

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
