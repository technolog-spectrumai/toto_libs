"""
Tests for toto.loans.services.

Covers the required scenarios:
1. App imports without loading forbidden modules (kanban/academy/mobilization/response).
2. create_loan_contract sets metadata archetype="loan".
3. create_repayment_duty creates obligation + approval condition.
4. settle_repayment delegates to fulfill_obligation.
5. Compatibility import from toto.contracts.loan still works.
6. create_loan_disbursement creates lender→borrower obligation.
7. Full lifecycle: disbursement → repayment duty → mark due → approve → settle.
"""
import warnings

from django.test import TestCase
from django.utils import timezone


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_asset(unit_name="LOAN", decimals=0):
    from toto.assets.models import Asset
    return Asset.objects.create(
        name=unit_name, unit_name=unit_name, decimals=decimals,
        total_supply_base_units=100_000_000, active=True,
    )


def _make_account(code, account_type="user"):
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.create(
        code=code, name=code, account_type=account_type, active=True,
    )


def _make_person(display_name="Person"):
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


def _make_loan_contract(**kwargs):
    from toto.loans.services import create_loan_contract
    asset = kwargs.get("asset") or _make_asset()
    lender = kwargs.get("lender_person") or _make_person("Lender")
    borrower = kwargs.get("borrower_person") or _make_person("Borrower")
    lender_acct = kwargs.get("lender_account") or _make_account("LENDER-001")
    borrower_acct = kwargs.get("borrower_account") or _make_account("BORROWER-001")
    return create_loan_contract(
        name=kwargs.get("name", "Test Loan"),
        lender_person=lender,
        borrower_person=borrower,
        lender_account=lender_acct,
        borrower_account=borrower_acct,
        loan_asset=asset,
        principal_base_units=kwargs.get("principal_base_units", 100_000),
        interest_rate_bps=kwargs.get("interest_rate_bps", 500),
        repayment_frequency=kwargs.get("repayment_frequency", "monthly"),
        term_months=kwargs.get("term_months", 12),
        metadata=kwargs.get("metadata"),
    )


# ---------------------------------------------------------------------------
# 1. Import safety
# ---------------------------------------------------------------------------

class LoanAppImportSafetyTest(TestCase):
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
# 2. create_loan_contract — Contract with archetype="loan"
# ---------------------------------------------------------------------------

class CreateLoanContractTest(TestCase):
    def test_creates_contract_with_loan_archetype(self):
        from toto.contracts.models import Contract
        contract = _make_loan_contract()
        self.assertIsNotNone(contract.pk)
        self.assertEqual(contract.metadata["archetype"], "loan")
        self.assertEqual(contract.status, Contract.STATUS_DRAFT)

    def test_contract_stores_loan_terms(self):
        contract = _make_loan_contract(
            principal_base_units=200_000,
            interest_rate_bps=750,
            repayment_frequency="quarterly",
            term_months=24,
        )
        self.assertEqual(contract.metadata["principal_base_units"], 200_000)
        self.assertEqual(contract.metadata["interest_rate_bps"], 750)
        self.assertEqual(contract.metadata["repayment_frequency"], "quarterly")
        self.assertEqual(contract.metadata["term_months"], 24)

    def test_expected_nodes_created(self):
        contract = _make_loan_contract()
        keys = set(contract.nodes.values_list("key", flat=True))
        self.assertSetEqual(
            keys,
            {"root", "lender_person", "borrower_person",
             "lender_acct", "borrower_acct", "loan_asset"},
        )


# ---------------------------------------------------------------------------
# 3. create_repayment_duty — obligation + approval condition
# ---------------------------------------------------------------------------

class CreateRepaymentDutyTest(TestCase):
    def setUp(self):
        self.asset = _make_asset("RPY")
        self.lender = _make_person("Lender R")
        self.borrower = _make_person("Borrower R")
        self.lender_acct = _make_account("LENDER-R")
        self.borrower_acct = _make_account("BORROWER-R")
        self.contract = _make_loan_contract(
            asset=self.asset,
            lender_person=self.lender,
            borrower_person=self.borrower,
            lender_account=self.lender_acct,
            borrower_account=self.borrower_acct,
        )

    def test_creates_obligation_and_condition(self):
        from toto.loans.services import create_repayment_duty
        from toto.assets.models import ObligationStatus
        from toto.claims.models import ConditionKind, ConditionStatus

        duty = create_repayment_duty(
            contract=self.contract,
            amount_base_units=10_000,
            due_at=timezone.now(),
        )

        ob = duty["obligation"]
        cond = duty["condition"]

        self.assertIsNotNone(ob.pk)
        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.debtor_account, self.borrower_acct)
        self.assertEqual(ob.creditor_account, self.lender_acct)

        self.assertIsNotNone(cond.pk)
        self.assertEqual(cond.status, ConditionStatus.PENDING)
        self.assertEqual(cond.kind, ConditionKind.APPROVAL)

    def test_repayment_nodes_added_to_graph(self):
        from toto.loans.services import create_repayment_duty
        create_repayment_duty(
            contract=self.contract,
            amount_base_units=5_000,
            due_at=timezone.now(),
        )
        self.assertTrue(
            self.contract.nodes.filter(key="repayment_1", node_type="obligation").exists()
        )
        self.assertTrue(
            self.contract.nodes.filter(key="repayment_approval_1", node_type="condition").exists()
        )

    def test_duty_index_increments(self):
        from toto.loans.services import create_repayment_duty
        create_repayment_duty(contract=self.contract, amount_base_units=5_000)
        create_repayment_duty(contract=self.contract, amount_base_units=5_000)
        self.assertTrue(self.contract.nodes.filter(key="repayment_2").exists())


# ---------------------------------------------------------------------------
# 4. create_loan_disbursement — lender → borrower obligation
# ---------------------------------------------------------------------------

class CreateLoanDisbursementTest(TestCase):
    def setUp(self):
        self.asset = _make_asset("DSB")
        self.lender = _make_person("Lender D")
        self.borrower = _make_person("Borrower D")
        self.lender_acct = _make_account("LENDER-D")
        self.borrower_acct = _make_account("BORROWER-D")
        self.contract = _make_loan_contract(
            asset=self.asset,
            lender_person=self.lender,
            borrower_person=self.borrower,
            lender_account=self.lender_acct,
            borrower_account=self.borrower_acct,
        )

    def test_creates_disbursement_obligation(self):
        from toto.loans.services import create_loan_disbursement
        from toto.assets.models import ObligationStatus

        result = create_loan_disbursement(
            contract=self.contract,
            amount_base_units=100_000,
        )
        ob = result["obligation"]
        self.assertIsNotNone(ob.pk)
        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.debtor_account, self.lender_acct)
        self.assertEqual(ob.creditor_account, self.borrower_acct)

    def test_disbursement_node_added_to_graph(self):
        from toto.loans.services import create_loan_disbursement
        create_loan_disbursement(contract=self.contract, amount_base_units=50_000)
        self.assertTrue(
            self.contract.nodes.filter(key="disbursement_1", node_type="obligation").exists()
        )


# ---------------------------------------------------------------------------
# 5. settle_repayment — delegates to fulfill_obligation
# ---------------------------------------------------------------------------

class SettleRepaymentTest(TestCase):
    def setUp(self):
        from toto.loans.services import (
            create_repayment_duty, approve_repayment,
        )
        self.asset = _make_asset("STL")
        self.lender = _make_person("Lender S")
        self.borrower = _make_person("Borrower S")
        self.lender_acct = _make_account("LENDER-S")
        self.borrower_acct = _make_account("BORROWER-S")
        self.contract = _make_loan_contract(
            asset=self.asset,
            lender_person=self.lender,
            borrower_person=self.borrower,
            lender_account=self.lender_acct,
            borrower_account=self.borrower_acct,
        )
        self.duty = create_repayment_duty(
            contract=self.contract,
            amount_base_units=5_000,
            due_at=timezone.now(),
        )
        _fund_account(self.borrower_acct, self.asset, 50_000)
        approve_repayment(condition=self.duty["condition"])

    def test_delegates_to_fulfill_obligation(self):
        from unittest.mock import patch
        from toto.assets.services import assets as assets_svc
        from toto.loans.services import settle_repayment

        with patch.object(
            assets_svc, "fulfill_obligation",
            wraps=assets_svc.fulfill_obligation,
        ) as mock_fulfill:
            result = settle_repayment(
                obligation=self.duty["obligation"],
                contract=self.contract,
            )
            mock_fulfill.assert_called_once()

        from toto.claims.models import ContractEventKind
        self.assertEqual(result["event"].kind, ContractEventKind.SETTLEMENT)
        self.assertEqual(result["event"].source_type, "contracts.Contract")

    def test_settle_updates_obligation_status(self):
        from toto.assets.models import ObligationStatus
        from toto.loans.services import settle_repayment
        settle_repayment(obligation=self.duty["obligation"], contract=self.contract)
        self.duty["obligation"].refresh_from_db()
        self.assertEqual(self.duty["obligation"].status, ObligationStatus.FULFILLED)


# ---------------------------------------------------------------------------
# 6. Compatibility shim — toto.contracts.loan still importable
# ---------------------------------------------------------------------------

class CompatibilityShimTest(TestCase):
    def test_contracts_loan_re_exports_all_public_api(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from toto.contracts import loan as shim  # noqa: F401

        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "Expected a DeprecationWarning from the shim",
        )

        from toto.loans import services
        for name in (
            "create_loan_contract",
            "create_loan_disbursement",
            "create_repayment_duty",
            "mark_repayment_due",
            "approve_repayment",
            "settle_repayment",
        ):
            self.assertIs(
                getattr(shim, name),
                getattr(services, name),
                f"shim.{name} should be the same object as services.{name}",
            )


# ---------------------------------------------------------------------------
# 7. Full lifecycle
# ---------------------------------------------------------------------------

class LoanLifecycleTest(TestCase):
    def setUp(self):
        self.asset = _make_asset("LIFE")
        self.lender = _make_person("Lender L")
        self.borrower = _make_person("Borrower L")
        self.lender_acct = _make_account("LENDER-L")
        self.borrower_acct = _make_account("BORROWER-L")
        self.contract = _make_loan_contract(
            asset=self.asset,
            lender_person=self.lender,
            borrower_person=self.borrower,
            lender_account=self.lender_acct,
            borrower_account=self.borrower_acct,
        )

    def test_full_lifecycle_event_sequence(self):
        from toto.loans.services import (
            create_loan_disbursement, create_repayment_duty,
            mark_repayment_due, approve_repayment, settle_repayment,
        )
        from toto.claims.models import ContractEvent, ContractEventKind

        _fund_account(self.lender_acct, self.asset, 100_000)
        _fund_account(self.borrower_acct, self.asset, 50_000)

        create_loan_disbursement(contract=self.contract, amount_base_units=100_000)
        duty = create_repayment_duty(contract=self.contract, amount_base_units=10_000)
        mark_repayment_due(contract=self.contract, duty_node_key="repayment_1")
        approve_repayment(condition=duty["condition"])
        settle_repayment(obligation=duty["obligation"], contract=self.contract)

        events = set(
            ContractEvent.objects.filter(
                source_type="contracts.Contract",
                source_id=str(self.contract.pk),
            ).values_list("kind", flat=True)
        )
        self.assertIn(ContractEventKind.OBLIGATION_CREATED, events)
        self.assertIn(ContractEventKind.PAYMENT_DUE, events)
        self.assertIn(ContractEventKind.CONDITION_SATISFIED, events)
        self.assertIn(ContractEventKind.SETTLEMENT, events)
