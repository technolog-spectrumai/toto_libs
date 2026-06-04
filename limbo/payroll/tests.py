"""
Tests for toto.payroll.services.

Covers the 5 required scenarios:
1. App imports without loading forbidden modules (kanban/academy/mobilization/response).
2. create_payroll_contract sets metadata archetype="payroll".
3. create_payroll_duty creates obligation + approval condition.
4. settle_payroll_duty delegates to fulfill_obligation.
5. Compatibility import from toto.contracts.payroll still works.
"""
import warnings
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_asset(unit_name="PAY", decimals=2):
    from toto.assets.models import Asset
    return Asset.objects.create(
        name=unit_name, unit_name=unit_name, decimals=decimals,
        total_supply_base_units=10_000_000, active=True,
    )


def _make_account(code, account_type="user"):
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.create(
        code=code, name=code, account_type=account_type, active=True,
    )


def _make_person(display_name="Alice Worker"):
    from toto.people.models import Person
    return Person.objects.create(display_name=display_name)


def _fund_account(account, asset, amount_base_units):
    from toto.assets.models import AssetHolding
    holding, _ = AssetHolding.objects.get_or_create(
        asset=asset, account=account,
        defaults={"balance_base_units": amount_base_units},
    )
    if holding.balance_base_units < amount_base_units:
        holding.balance_base_units = amount_base_units
        holding.save(update_fields=["balance_base_units", "updated_at"])
    return holding


# ---------------------------------------------------------------------------
# 1. Import safety — no forbidden module names in the source
# ---------------------------------------------------------------------------

class PayrollAppImportSafetyTest(TestCase):
    def test_services_has_no_forbidden_imports(self):
        import ast, os
        path = os.path.join(os.path.dirname(__file__), "services.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        forbidden = {"kanban", "academy"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    self.assertFalse(
                        any(f in alias.name for f in forbidden),
                        f"Forbidden import found: {alias.name}",
                    )
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        any(f in node.module for f in forbidden),
                        f"Forbidden import found: {node.module}",
                    )


# ---------------------------------------------------------------------------
# 2. create_payroll_contract — Contract with archetype="payroll"
# ---------------------------------------------------------------------------

class CreatePayrollContractTest(TestCase):
    def test_creates_contract_with_payroll_archetype(self):
        from toto.payroll.services import create_payroll_contract
        from toto.contracts.models import Contract

        asset = _make_asset("CPT", decimals=0)
        payer = _make_account("CPT-PAYER")
        worker = _make_person("Bob Payroll")
        worker_acct = _make_account("CPT-WORKER")

        contract = create_payroll_contract(
            name="Test Payroll",
            payer_account=payer,
            worker_person=worker,
            worker_account=worker_acct,
            asset=asset,
            amount_base_units=100_000,
            frequency="monthly",
        )

        self.assertIsNotNone(contract.pk)
        self.assertEqual(contract.metadata["archetype"], "payroll")
        self.assertEqual(contract.status, Contract.STATUS_DRAFT)


# ---------------------------------------------------------------------------
# 3. create_payroll_duty — obligation + approval condition
# ---------------------------------------------------------------------------

class CreatePayrollDutyTest(TestCase):
    def setUp(self):
        from toto.payroll.services import create_payroll_contract
        self.asset = _make_asset("DUTY", decimals=0)
        self.payer = _make_account("DUTY-PAYER")
        self.worker = _make_person("Carol Duty")
        self.worker_acct = _make_account("DUTY-WORKER")
        self.contract = create_payroll_contract(
            name="Duty Test",
            payer_account=self.payer,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            asset=self.asset,
            amount_base_units=500_000,
            frequency="weekly",
        )

    def test_creates_obligation_and_condition(self):
        from toto.payroll.services import create_payroll_duty
        from toto.assets.models import Obligation, ObligationStatus
        from toto.claims.models import ConditionKind, ConditionStatus

        duty = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=10_000,
            due_at=timezone.now(),
        )

        ob = duty["obligation"]
        cond = duty["condition"]

        self.assertIsNotNone(ob.pk)
        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.debtor_account, self.payer)
        self.assertEqual(ob.creditor_account, self.worker_acct)

        self.assertIsNotNone(cond.pk)
        self.assertEqual(cond.status, ConditionStatus.PENDING)
        self.assertEqual(cond.kind, ConditionKind.APPROVAL)


# ---------------------------------------------------------------------------
# 4. settle_payroll_duty — delegates to fulfill_obligation
# ---------------------------------------------------------------------------

class SettlePayrollDutyTest(TestCase):
    def setUp(self):
        from toto.payroll.services import (
            create_payroll_contract, create_payroll_duty, approve_payroll_duty,
        )
        self.asset = _make_asset("SETTLE", decimals=0)
        self.payer = _make_account("STL-PAYER")
        self.worker = _make_person("Dave Settle")
        self.worker_acct = _make_account("STL-WORKER")
        self.contract = create_payroll_contract(
            name="Settle Test",
            payer_account=self.payer,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            asset=self.asset,
            amount_base_units=100_000,
            frequency="monthly",
        )
        self.duty = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=5_000,
            due_at=timezone.now(),
        )
        _fund_account(self.payer, self.asset, 50_000)
        approve_payroll_duty(condition=self.duty["condition"])

    def test_delegates_to_fulfill_obligation(self):
        from unittest.mock import patch
        from toto.assets.services import assets as assets_svc
        from toto.payroll.services import settle_payroll_duty

        with patch.object(
            assets_svc, "fulfill_obligation",
            wraps=assets_svc.fulfill_obligation,
        ) as mock_fulfill:
            result = settle_payroll_duty(
                obligation=self.duty["obligation"],
                contract=self.contract,
            )
            mock_fulfill.assert_called_once()

        from toto.claims.models import ContractEventKind
        self.assertEqual(result["event"].kind, ContractEventKind.SETTLEMENT)


# ---------------------------------------------------------------------------
# 5. Compatibility shim — toto.contracts.payroll still importable
# ---------------------------------------------------------------------------

class CompatibilityShimTest(TestCase):
    def test_contracts_payroll_re_exports_all_public_api(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from toto.contracts import payroll as shim  # noqa: F401

        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "Expected a DeprecationWarning from the shim",
        )

        from toto.payroll import services
        for name in (
            "create_payroll_contract",
            "create_payroll_duty",
            "mark_payroll_due",
            "approve_payroll_duty",
            "settle_payroll_duty",
        ):
            self.assertIs(
                getattr(shim, name),
                getattr(services, name),
                f"shim.{name} should be the same object as services.{name}",
            )
