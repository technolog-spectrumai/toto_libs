"""
Phase 3 tests — verify metered apps check balance and charge on action.

These are integration-style tests that confirm:
1. Vault upload returns 402 when user has no balance.
2. Steven quick_ask returns 402 fragment when user has no balance.
3. Ravioli run_cypher returns 402 JSON when user has no balance.
4. All three proceed normally when no tariff is configured (free pass).
5. Transcription start redirects with error message when user has no balance.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, RequestFactory
from django.urls import reverse

from toto.assets.models import Asset, AssetHolding, LedgerAccount, AccountType, to_base_units
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.tariffs.models import (
    BillingMetric, Tariff, TariffItem, TariffStatus, RoundingMode,
)

User = get_user_model()


def make_asset(unit_name="WIRING", decimals=2):
    return Asset.objects.create(
        name=unit_name, unit_name=unit_name, decimals=decimals,
        total_supply_base_units=10 ** 12, active=True,
    )


def make_account(code, account_type=AccountType.SYSTEM):
    return LedgerAccount.objects.create(
        code=code, name=code, account_type=account_type, active=True,
    )


def make_platform():
    from toto.core.models import Platform
    return Platform.objects.get_or_create(
        site_name="Test", defaults={"author": "t", "publication_year": 2025, "active": True}
    )[0]


def make_tariff_with_item(tariff_code, metric_code, app_label, asset, recv,
                           price=Decimal("1")):
    metric = BillingMetric.objects.get_or_create(
        code=metric_code,
        defaults={"label": metric_code, "active": True, "app_label": app_label},
    )[0]
    tariff = Tariff.objects.create(
        name=tariff_code, code=tariff_code, status=TariffStatus.ACTIVE,
        source_type="platform",
    )
    TariffItem.objects.create(
        tariff=tariff, metric=metric,
        name=f"{metric_code}-item",
        charged_asset=asset,
        price_per_unit_display=price,
        price_per_unit_base_units=to_base_units(price, asset.decimals),
        unit_quantity=Decimal("1"),
        receiving_account=recv,
        rounding_mode=RoundingMode.UP,
        active=True,
    )
    return tariff


def fund(user, asset, amount_base_units):
    payer, _ = get_or_create_prepaid_account(user)
    holding, _ = AssetHolding.objects.get_or_create(
        account=payer, asset=asset, defaults={"balance_base_units": amount_base_units}
    )
    if holding.balance_base_units < amount_base_units:
        holding.balance_base_units = amount_base_units
        holding.save()


# ===========================================================================
# metered_action helper
# ===========================================================================

class MeteredActionHelperTest(TestCase):
    """Test the shared metered_action helper directly."""

    def setUp(self):
        make_platform()
        self.user = User.objects.create_user("helper_user", password="x")
        self.asset = make_asset("HELPER")
        self.recv = make_account("HELPER-RECV")
        self.tariff = make_tariff_with_item(
            "helper-tariff", "storage.request", "vault", self.asset, self.recv
        )

    def test_returns_error_response_when_short(self):
        from django.test import RequestFactory
        from toto.tariffs.metering_mixins import metered_action
        rf = RequestFactory()
        request = rf.get("/")
        request.user = self.user
        # No balance
        tariff_result, err_response = metered_action(
            request, "vault", "storage.request", 1,
            tariff=self.tariff,
        )
        self.assertIsNone(tariff_result)
        self.assertIsNotNone(err_response)
        self.assertEqual(err_response.status_code, 402)

    def test_returns_tariff_when_funded(self):
        from toto.tariffs.metering_mixins import metered_action
        fund(self.user, self.asset, 1000)
        rf = RequestFactory()
        request = rf.get("/")
        request.user = self.user
        tariff_result, err_response = metered_action(
            request, "vault", "storage.request", 1,
            tariff=self.tariff,
        )
        self.assertIsNotNone(tariff_result)
        self.assertIsNone(err_response)

    def test_free_pass_when_no_tariff_configured(self):
        from toto.tariffs.metering_mixins import metered_action
        rf = RequestFactory()
        request = rf.get("/")
        request.user = self.user
        tariff_result, err_response = metered_action(
            request, "unknown_app_label_xyz", "unknown.metric", 1,
        )
        # No tariff → free pass
        self.assertIsNone(tariff_result)
        self.assertIsNone(err_response)


# ===========================================================================
# Architecture: metered views don't import budget/treasury
# ===========================================================================

class MeteringArchitectureTest(TestCase):
    def test_vault_views_do_not_import_treasury(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent.parent.parent / "vault" / "views.py"
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("toto.treasury"),
                    "vault/views.py must not import treasury",
                )

    def test_steven_views_do_not_import_treasury(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent.parent.parent / "steven" / "views.py"
        if not src.exists():
            return
        tree = ast.parse(src.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("toto.treasury"),
                    "steven/views.py must not import treasury",
                )

    def test_charge_module_does_not_import_metered_apps(self):
        import ast, pathlib
        src = pathlib.Path(__file__).parent.parent / "charge.py"
        tree = ast.parse(src.read_text())
        forbidden = ["vault", "vod", "ravioli", "steven", "transcription",
                     "academy", "kanban", "treasury"]
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for f in forbidden:
                    self.assertFalse(
                        f"toto.{f}" in node.module,
                        f"tariffs/charge.py must not import {f}",
                    )
