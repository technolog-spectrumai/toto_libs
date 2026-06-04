"""
Tests for the payroll contract archetype (contracts/payroll.py).
"""
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from .models import Contract, ContractEdge, ContractNode
from toto.payroll.services import (
    approve_payroll_duty,
    create_payroll_contract,
    create_payroll_duty,
    mark_payroll_due,
    settle_payroll_duty,
)


# ---------------------------------------------------------------------------
# Helpers
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
        code=code, name=code, account_type=account_type, active=True
    )


def _make_person(display_name="Alice Worker"):
    from toto.people.models import Person
    return Person.objects.create(display_name=display_name)


def _fund_account(account, asset, amount_base_units):
    """Directly set an AssetHolding balance so transfer_asset can succeed."""
    from toto.assets.models import AssetHolding
    holding, _ = AssetHolding.objects.get_or_create(
        asset=asset, account=account,
        defaults={"balance_base_units": amount_base_units},
    )
    if holding.balance_base_units < amount_base_units:
        holding.balance_base_units = amount_base_units
        holding.save(update_fields=["balance_base_units", "updated_at"])
    return holding


def _make_payroll_contract(**kwargs):
    asset = kwargs.get("asset") or _make_asset()
    payer = kwargs.get("payer_account") or _make_account("PAYER-001")
    worker = kwargs.get("worker_person") or _make_person()
    worker_acct = kwargs.get("worker_account") or _make_account("WORKER-001")
    return create_payroll_contract(
        name=kwargs.get("name", "Monthly Payroll"),
        payer_account=payer,
        worker_person=worker,
        worker_account=worker_acct,
        asset=asset,
        amount_base_units=kwargs.get("amount_base_units", 500_000),
        frequency=kwargs.get("frequency", "monthly"),
        metadata=kwargs.get("metadata"),
    )


# ---------------------------------------------------------------------------
# Import safety
# ---------------------------------------------------------------------------

class PayrollImportSafetyTests(TestCase):
    def test_no_forbidden_imports(self):
        import ast, os
        # Check the canonical source, not the shim
        path = os.path.join(os.path.dirname(__file__), "..", "payroll", "services.py")
        path = os.path.normpath(path)
        with open(path) as f:
            tree = ast.parse(f.read())
        forbidden = {"kanban", "academy", "deployment"}
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in getattr(node, "names", []):
                    assert not any(f in alias.name for f in forbidden), (
                        f"Forbidden import found: {alias.name}"
                    )
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert not any(f in node.module for f in forbidden), (
                        f"Forbidden import found: {node.module}"
                    )


# ---------------------------------------------------------------------------
# create_payroll_contract
# ---------------------------------------------------------------------------

class CreatePayrollContractTests(TestCase):
    def setUp(self):
        self.asset = _make_asset()
        self.payer = _make_account("PAYER-C-01")
        self.worker = _make_person("Bob Payee")
        self.worker_acct = _make_account("WORKER-C-01")
        self.contract = create_payroll_contract(
            name="Weekly Payroll",
            payer_account=self.payer,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            asset=self.asset,
            amount_base_units=200_000,
            frequency="weekly",
        )

    def test_contract_created_draft(self):
        self.assertEqual(self.contract.status, Contract.STATUS_DRAFT)
        self.assertEqual(self.contract.metadata["archetype"], "payroll")

    def test_expected_nodes_created(self):
        keys = set(self.contract.nodes.values_list("key", flat=True))
        self.assertSetEqual(
            keys,
            {"root", "payer_acct", "worker_person", "worker_acct",
             "payment_asset", "pay_schedule", "budget"},
        )

    def test_root_node_backed_by_contract(self):
        root = self.contract.nodes.get(key="root")
        self.assertEqual(root.node_type, "contract")
        self.assertEqual(root.object_app, "contracts")
        self.assertEqual(root.object_model, "contract")
        self.assertEqual(root.object_id, str(self.contract.pk))

    def test_payer_and_worker_nodes_backed(self):
        payer_node = self.contract.nodes.get(key="payer_acct")
        worker_node = self.contract.nodes.get(key="worker_acct")
        self.assertEqual(payer_node.object_id, str(self.payer.pk))
        self.assertEqual(worker_node.object_id, str(self.worker_acct.pk))

    def test_schedule_node_backed_by_claims_schedule(self):
        sched_node = self.contract.nodes.get(key="pay_schedule")
        self.assertEqual(sched_node.node_type, "schedule")
        self.assertEqual(sched_node.object_app, "claims")
        obj = sched_node.get_object()
        self.assertIsNotNone(obj)
        self.assertEqual(obj.frequency, "weekly")
        self.assertEqual(obj.source_type, "contracts.Contract")
        self.assertEqual(obj.source_id, str(self.contract.pk))

    def test_allocation_node_backed_by_claims_allocation(self):
        budget_node = self.contract.nodes.get(key="budget")
        self.assertEqual(budget_node.node_type, "allocation")
        obj = budget_node.get_object()
        self.assertIsNotNone(obj)
        self.assertEqual(obj.amount_base_units, 200_000)
        self.assertEqual(obj.source_type, "contracts.Contract")

    def test_edges_created(self):
        edge_types = set(
            self.contract.edges.values_list("edge_type", flat=True)
        )
        self.assertIn("creates_duty", edge_types)
        self.assertIn("allocates", edge_types)
        self.assertIn("debtor", edge_types)
        self.assertIn("creditor", edge_types)
        self.assertIn("uses_asset", edge_types)
        self.assertIn("related", edge_types)

    def test_metadata_carries_external_source_ref(self):
        contract = create_payroll_contract(
            name="External Payroll",
            payer_account=_make_account("EXT-PAYER"),
            worker_person=self.worker,
            worker_account=_make_account("EXT-WORKER"),
            asset=self.asset,
            amount_base_units=100_000,
            frequency="monthly",
            metadata={
                "source_app": "hrm",
                "source_model": "EmployeeContract",
                "source_id": "42",
            },
        )
        self.assertEqual(contract.metadata["source_app"], "hrm")
        self.assertEqual(contract.metadata["source_id"], "42")
        self.assertEqual(contract.metadata["archetype"], "payroll")


# ---------------------------------------------------------------------------
# create_payroll_duty
# ---------------------------------------------------------------------------

class CreatePayrollDutyTests(TestCase):
    def setUp(self):
        self.asset = _make_asset("DUTYTOKEN", decimals=0)
        self.payer = _make_account("DPAYER-01")
        self.worker = _make_person("Carol Duty")
        self.worker_acct = _make_account("DWORKER-01")
        self.contract = create_payroll_contract(
            name="Duty Payroll",
            payer_account=self.payer,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            asset=self.asset,
            amount_base_units=1_000_000,
            frequency="monthly",
        )
        self.duty = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=50_000,
            due_at=timezone.now(),
        )

    def test_obligation_created(self):
        from toto.assets.models import Obligation, ObligationStatus
        ob = self.duty["obligation"]
        self.assertIsNotNone(ob.pk)
        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.amount_base_units, 50_000)
        self.assertEqual(ob.debtor_account, self.payer)
        self.assertEqual(ob.creditor_account, self.worker_acct)

    def test_condition_created_pending(self):
        from toto.claims.models import ConditionKind, ConditionStatus
        cond = self.duty["condition"]
        self.assertIsNotNone(cond.pk)
        self.assertEqual(cond.status, ConditionStatus.PENDING)
        self.assertEqual(cond.kind, ConditionKind.APPROVAL)
        self.assertEqual(cond.source_type, "contracts.Contract")
        self.assertEqual(cond.source_id, str(self.contract.pk))

    def test_obligation_created_event_emitted(self):
        from toto.claims.models import ContractEventKind
        event = self.duty["event"]
        self.assertEqual(event.kind, ContractEventKind.OBLIGATION_CREATED)
        self.assertEqual(event.obligation, self.duty["obligation"])
        self.assertEqual(event.condition, self.duty["condition"])

    def test_duty_node_added_to_graph(self):
        self.assertTrue(
            self.contract.nodes.filter(key="duty_1", node_type="obligation").exists()
        )

    def test_approval_node_added_to_graph(self):
        self.assertTrue(
            self.contract.nodes.filter(key="approval_1", node_type="condition").exists()
        )

    def test_gates_edge_from_approval_to_duty(self):
        edge = ContractEdge.objects.get(
            contract=self.contract,
            source__key="approval_1",
            target__key="duty_1",
            edge_type="gates",
        )
        self.assertIsNotNone(edge)

    def test_duty_index_increments_on_second_duty(self):
        duty2 = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=25_000,
            due_at=timezone.now(),
        )
        self.assertTrue(
            self.contract.nodes.filter(key="duty_2").exists()
        )
        self.assertTrue(
            self.contract.nodes.filter(key="approval_2").exists()
        )

    def test_metadata_forwarded_to_obligation_event(self):
        duty = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=10_000,
            due_at=timezone.now(),
            metadata={"source_app": "shifts", "source_id": "99"},
        )
        self.assertEqual(duty["event"].payload["source_app"], "shifts")
        self.assertEqual(duty["condition"].metadata["source_id"], "99")


# ---------------------------------------------------------------------------
# mark_payroll_due
# ---------------------------------------------------------------------------

class MarkPayrollDueTests(TestCase):
    def setUp(self):
        self.asset = _make_asset("DUE", decimals=0)
        self.payer = _make_account("DUEPAYER")
        self.worker = _make_person("Dave Due")
        self.worker_acct = _make_account("DUEWORKER")
        self.contract = create_payroll_contract(
            name="Due Payroll",
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
            amount_base_units=20_000,
            due_at=timezone.now(),
        )

    def test_payment_due_event_created(self):
        from toto.claims.models import ContractEventKind
        event = mark_payroll_due(contract=self.contract, duty_node_key="duty_1")
        self.assertEqual(event.kind, ContractEventKind.PAYMENT_DUE)
        self.assertEqual(event.obligation, self.duty["obligation"])
        self.assertEqual(event.source_type, "contracts.Contract")
        self.assertEqual(event.source_id, str(self.contract.pk))

    def test_event_payload_carries_archetype(self):
        event = mark_payroll_due(
            contract=self.contract, duty_node_key="duty_1",
            metadata={"period": "2026-05"},
        )
        self.assertEqual(event.payload["archetype"], "payroll")
        self.assertEqual(event.payload["period"], "2026-05")


# ---------------------------------------------------------------------------
# approve_payroll_duty
# ---------------------------------------------------------------------------

class ApprovePayrollDutyTests(TestCase):
    def setUp(self):
        self.asset = _make_asset("APR", decimals=0)
        self.payer = _make_account("APRPAYER")
        self.worker = _make_person("Eve Approver")
        self.worker_acct = _make_account("APRWORKER")
        self.contract = create_payroll_contract(
            name="Approval Payroll",
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
            amount_base_units=10_000,
            due_at=timezone.now(),
        )

    def test_condition_becomes_satisfied(self):
        from toto.claims.models import ConditionStatus
        condition = self.duty["condition"]
        approve_payroll_duty(condition=condition)
        condition.refresh_from_db()
        self.assertEqual(condition.status, ConditionStatus.SATISFIED)
        self.assertIsNotNone(condition.satisfied_at)

    def test_condition_satisfied_event_created(self):
        from toto.claims.models import ContractEventKind
        condition = self.duty["condition"]
        event = approve_payroll_duty(condition=condition)
        self.assertEqual(event.kind, ContractEventKind.CONDITION_SATISFIED)
        self.assertEqual(event.condition, condition)

    def test_approver_recorded_on_event(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        approver = User.objects.create_user(username="mgr1", password="x")
        condition = self.duty["condition"]
        event = approve_payroll_duty(condition=condition, approver=approver)
        self.assertEqual(event.actor, approver)


# ---------------------------------------------------------------------------
# settle_payroll_duty
# ---------------------------------------------------------------------------

class SettlePayrollDutyTests(TestCase):
    def setUp(self):
        self.asset = _make_asset("SETTLE", decimals=0)
        self.payer = _make_account("SETPAYER")
        self.worker = _make_person("Frank Settle")
        self.worker_acct = _make_account("SETWORKER")
        self.contract = create_payroll_contract(
            name="Settlement Payroll",
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

    def test_settlement_uses_assets_backend(self):
        """fulfill_obligation from assets.services must be the settling mechanism."""
        from toto.assets.models import ObligationStatus
        obligation = self.duty["obligation"]
        result = settle_payroll_duty(
            obligation=obligation, contract=self.contract
        )
        obligation.refresh_from_db()
        self.assertEqual(obligation.status, ObligationStatus.FULFILLED)
        self.assertIsNotNone(obligation.fulfilled_at)

    def test_settlement_returns_transaction(self):
        from toto.assets.models import LedgerTransaction
        result = settle_payroll_duty(
            obligation=self.duty["obligation"], contract=self.contract
        )
        self.assertIn("transaction", result)
        self.assertIsInstance(result["transaction"], LedgerTransaction)

    def test_settlement_event_created(self):
        from toto.claims.models import ContractEventKind
        result = settle_payroll_duty(
            obligation=self.duty["obligation"], contract=self.contract
        )
        event = result["event"]
        self.assertEqual(event.kind, ContractEventKind.SETTLEMENT)
        self.assertEqual(event.obligation, self.duty["obligation"])
        self.assertEqual(event.transaction, result["transaction"])

    def test_worker_balance_increases(self):
        from toto.assets.models import AssetHolding
        settle_payroll_duty(
            obligation=self.duty["obligation"], contract=self.contract
        )
        holding = AssetHolding.objects.get(asset=self.asset, account=self.worker_acct)
        self.assertEqual(holding.balance_base_units, 5_000)

    def test_payer_balance_decreases(self):
        from toto.assets.models import AssetHolding
        settle_payroll_duty(
            obligation=self.duty["obligation"], contract=self.contract
        )
        holding = AssetHolding.objects.get(asset=self.asset, account=self.payer)
        self.assertEqual(holding.balance_base_units, 45_000)

    def test_settle_uses_assets_fulfill_obligation_not_direct_mutation(self):
        """Ensure settlement path calls fulfill_obligation, not direct balance update."""
        from unittest.mock import patch
        from toto.assets.services import assets as assets_svc
        obligation = self.duty["obligation"]
        with patch.object(
            assets_svc, "fulfill_obligation",
            wraps=assets_svc.fulfill_obligation,
        ) as mock_fulfill:
            settle_payroll_duty(obligation=obligation, contract=self.contract)
            mock_fulfill.assert_called_once()

    def test_settlement_event_source_type_is_contract(self):
        result = settle_payroll_duty(
            obligation=self.duty["obligation"], contract=self.contract
        )
        self.assertEqual(result["event"].source_type, "contracts.Contract")
        self.assertEqual(result["event"].source_id, str(self.contract.pk))


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

class PayrollAuditTrailTests(TestCase):
    def setUp(self):
        self.asset = _make_asset("AUDIT", decimals=0)
        self.payer = _make_account("AUDITPAYER")
        self.worker = _make_person("Grace Audit")
        self.worker_acct = _make_account("AUDITWORKER")
        self.contract = create_payroll_contract(
            name="Audit Payroll",
            payer_account=self.payer,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            asset=self.asset,
            amount_base_units=100_000,
            frequency="monthly",
        )

    def test_full_lifecycle_produces_correct_event_sequence(self):
        from toto.claims.models import ContractEvent, ContractEventKind
        _fund_account(self.payer, self.asset, 100_000)

        duty = create_payroll_duty(
            contract=self.contract,
            worker_person=self.worker,
            worker_account=self.worker_acct,
            amount_base_units=10_000,
            due_at=timezone.now(),
        )
        mark_payroll_due(contract=self.contract, duty_node_key="duty_1")
        approve_payroll_duty(condition=duty["condition"])
        settle_payroll_duty(obligation=duty["obligation"], contract=self.contract)

        events = ContractEvent.objects.filter(
            source_type="contracts.Contract",
            source_id=str(self.contract.pk),
        ).values_list("kind", flat=True)

        event_set = set(events)
        self.assertIn(ContractEventKind.OBLIGATION_CREATED, event_set)
        self.assertIn(ContractEventKind.PAYMENT_DUE, event_set)
        self.assertIn(ContractEventKind.CONDITION_SATISFIED, event_set)
        self.assertIn(ContractEventKind.SETTLEMENT, event_set)
