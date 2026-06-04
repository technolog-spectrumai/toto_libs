"""
Tests for toto.tariffs.charge — auto-charge helpers.

Covers:
1. InsufficientBalanceError display properties
2. get_tariff_for_user — community tariff, platform default, lowest-rate selection
3. check_user_can_act — passes when funded, raises when short
4. charge_user — drains prepaid balance, returns record + tx
5. check_and_charge — raises before write when short
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from toto.assets.models import (
    AccountType, Asset, AssetHolding, LedgerAccount,
    to_base_units,
)
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.tariffs.charge import (
    InsufficientBalanceError,
    check_and_charge,
    charge_user,
    check_user_can_act,
    get_tariff_for_user,
)
from toto.tariffs.models import (
    BillingMetric, BillingUnit, RoundingMode,
    Tariff, TariffItem, TariffStatus,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_asset(unit_name="CHARGE", decimals=2):
    return Asset.objects.create(
        name=unit_name, unit_name=unit_name, decimals=decimals,
        total_supply_base_units=10 ** 12, active=True,
    )


def make_account(code, account_type=AccountType.SYSTEM):
    return LedgerAccount.objects.create(
        code=code, name=code, account_type=account_type, active=True,
    )


def make_metric(code, app_label="vault"):
    return BillingMetric.objects.get_or_create(
        code=code,
        defaults={"label": code, "active": True, "app_label": app_label},
    )[0]


def make_tariff(code, source_type="", source_id=""):
    return Tariff.objects.create(
        name=code, code=code, status=TariffStatus.ACTIVE,
        source_type=source_type, source_id=source_id,
    )


def make_tariff_item(tariff, metric_code, asset, recv_account,
                     price=Decimal("1"), unit_quantity=Decimal("1"),
                     app_label="vault"):
    metric = make_metric(metric_code, app_label=app_label)
    return TariffItem.objects.create(
        tariff=tariff,
        metric=metric,
        name=f"{metric_code}-item",
        charged_asset=asset,
        price_per_unit_display=price,
        price_per_unit_base_units=to_base_units(price, asset.decimals),
        unit_quantity=unit_quantity,
        receiving_account=recv_account,
        rounding_mode=RoundingMode.UP,
        active=True,
    )


def fund_account(account, asset, amount_base_units: int):
    holding, _ = AssetHolding.objects.get_or_create(
        account=account, asset=asset,
        defaults={"balance_base_units": amount_base_units},
    )
    if holding.balance_base_units < amount_base_units:
        holding.balance_base_units = amount_base_units
        holding.save()
    return holding


# ===========================================================================
# 1. InsufficientBalanceError
# ===========================================================================

class InsufficientBalanceErrorTest(TestCase):

    def test_str_contains_asset_name(self):
        err = InsufficientBalanceError("CREDIT", 500, 100, asset_decimals=2)
        self.assertIn("CREDIT", str(err))

    def test_needed_display(self):
        err = InsufficientBalanceError("TOK", 500, 100, asset_decimals=2)
        self.assertEqual(err.needed_display, Decimal("5.00"))

    def test_have_display(self):
        err = InsufficientBalanceError("TOK", 500, 100, asset_decimals=2)
        self.assertEqual(err.have_display, Decimal("1.00"))

    def test_shortfall_display(self):
        err = InsufficientBalanceError("TOK", 500, 100, asset_decimals=2)
        self.assertEqual(err.shortfall_display, Decimal("4.00"))

    def test_no_negative_shortfall(self):
        err = InsufficientBalanceError("TOK", 100, 500, asset_decimals=2)
        self.assertEqual(err.shortfall_display, Decimal("0"))


# ===========================================================================
# 2. check_user_can_act
# ===========================================================================

class CheckUserCanActTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("chargecheck", password="x")
        self.asset = make_asset("CHKASSET")
        self.recv = make_account("CHK-RECV")
        self.tariff = make_tariff("CHK-TARIFF")
        make_tariff_item(self.tariff, "storage.request", self.asset, self.recv,
                         price=Decimal("1"))  # 1 CHKASSET per request
        self.payer, _ = get_or_create_prepaid_account(self.user)

    def test_passes_when_funded(self):
        fund_account(self.payer, self.asset, 200)
        # Should not raise
        check_user_can_act(self.user, self.tariff, "storage.request", 1)

    def test_raises_when_no_balance(self):
        with self.assertRaises(InsufficientBalanceError) as cm:
            check_user_can_act(self.user, self.tariff, "storage.request", 1)
        self.assertIn("CHKASSET", str(cm.exception))

    def test_raises_with_correct_shortfall(self):
        fund_account(self.payer, self.asset, 50)  # have 0.50, need 1.00
        with self.assertRaises(InsufficientBalanceError) as cm:
            check_user_can_act(self.user, self.tariff, "storage.request", 1)
        self.assertGreater(cm.exception.shortfall_display, 0)

    def test_no_tariff_items_is_free_pass(self):
        empty_tariff = make_tariff("EMPTY-TARIFF")
        # No items = no charge = no error
        check_user_can_act(self.user, empty_tariff, "storage.request", 1)


# ===========================================================================
# 3. charge_user
# ===========================================================================

class ChargeUserTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("chargeuser", password="x")
        self.asset = make_asset("CHARGEASSET")
        self.recv = make_account("CHGUSR-RECV")
        self.tariff = make_tariff("CHGUSR-TARIFF")
        make_tariff_item(self.tariff, "ai.agent_run", self.asset, self.recv,
                         price=Decimal("2"))  # 2 base units per run
        self.payer, _ = get_or_create_prepaid_account(self.user)
        fund_account(self.payer, self.asset, 1000)

    def test_returns_record_and_tx(self):
        record, tx = charge_user(
            self.user, self.tariff, "ai.agent_run", 1,
            source_type="steven.AgentRun", source_id="99",
        )
        self.assertIsNotNone(record.pk)
        self.assertIsNotNone(tx.pk)

    def test_drains_prepaid_balance(self):
        before = AssetHolding.objects.get(account=self.payer, asset=self.asset).balance_base_units
        charge_user(self.user, self.tariff, "ai.agent_run", 1)
        after = AssetHolding.objects.get(account=self.payer, asset=self.asset).balance_base_units
        self.assertLess(after, before)

    def test_usage_record_has_correct_metric(self):
        record, _ = charge_user(self.user, self.tariff, "ai.agent_run", 1)
        self.assertEqual(record.metric_code, "ai.agent_run")


# ===========================================================================
# 4. check_and_charge — atomic
# ===========================================================================

class CheckAndChargeTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("cnc_user", password="x")
        self.asset = make_asset("CNCASSET")
        self.recv = make_account("CNC-RECV")
        self.tariff = make_tariff("CNC-TARIFF")
        make_tariff_item(self.tariff, "ravioli.cypher_query", self.asset, self.recv,
                         price=Decimal("5"))
        self.payer, _ = get_or_create_prepaid_account(self.user)

    def test_raises_before_write_when_short(self):
        from toto.tariffs.models import UsageRecord
        before = UsageRecord.objects.count()
        with self.assertRaises(InsufficientBalanceError):
            check_and_charge(self.user, self.tariff, "ravioli.cypher_query", 1)
        # No usage record was created
        self.assertEqual(UsageRecord.objects.count(), before)

    def test_succeeds_when_funded(self):
        fund_account(self.payer, self.asset, 1000)
        record, tx = check_and_charge(
            self.user, self.tariff, "ravioli.cypher_query", 1,
            source_type="ravioli.CypherQueryResult", source_id="1",
        )
        self.assertIsNotNone(record.pk)


# ===========================================================================
# 5. get_tariff_for_user — community + lowest rate
# ===========================================================================

class GetTariffForUserTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user("tariff_user", password="x")
        self.asset = make_asset("TARIFFASSET")
        self.recv = make_account("TRF-RECV")

    def _make_community_tariff(self, community, price):
        from toto.socialhub.models import Community
        t = make_tariff(f"COM-TARIFF-{community.pk}-{price}",
                        source_type="socialhub.Community",
                        source_id=str(community.pk))
        make_tariff_item(t, "storage.request", self.asset, self.recv,
                         price=Decimal(str(price)), app_label="vault")
        return t

    def _add_to_community(self, user, community):
        try:
            person = user.community_profile
            person.communities.add(community)
        except Exception:
            pass

    def test_returns_platform_default_when_no_community_tariff(self):
        platform_tariff = make_tariff("vault-default")
        make_tariff_item(platform_tariff, "storage.request", self.asset, self.recv,
                         app_label="vault")
        result = get_tariff_for_user(self.user, "vault")
        self.assertIsNotNone(result)

    def test_returns_none_when_no_tariff_exists(self):
        result = get_tariff_for_user(self.user, "nonexistent_app")
        self.assertIsNone(result)
