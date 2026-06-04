"""
Tests for toto.insurance.services.

Covers the required scenarios:
1. App imports without loading forbidden modules.
2. create_insurance_contract sets metadata archetype="insurance".
3. create_premium_duty creates obligation (payer→insurer) + approval condition.
4. create_claim_duty creates obligation (insurer→insured) + approval condition.
5. settle_premium delegates to fulfill_obligation.
6. settle_claim delegates to fulfill_obligation.
7. Compatibility import from toto.contracts.insurance still works.
8. Full lifecycle: premium duty → mark due → approve → settle.
9. Full claim lifecycle: claim duty → approve → settle.
"""
import warnings

from django.test import TestCase
from django.utils import timezone


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_asset(unit_name="INS", decimals=0):
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


def _make_insurance_contract(**kwargs):
    from toto.insurance.services import create_insurance_contract
    asset = kwargs.get("asset") or _make_asset()
    insured = kwargs.get("insured_person") or _make_person("Insured")
    payer_acct = kwargs.get("payer_account") or _make_account("INS-PAYER")
    insurer_acct = kwargs.get("insurer_account") or _make_account("INS-INSURER")
    return create_insurance_contract(
        name=kwargs.get("name", "Test Policy"),
        insured_person=insured,
        payer_account=payer_acct,
        insurer_account=insurer_acct,
        premium_asset=asset,
        premium_amount_base_units=kwargs.get("premium_amount_base_units", 1_000),
        premium_frequency=kwargs.get("premium_frequency", "monthly"),
        insured_items=kwargs.get("insured_items", []),
        insured_financial_assets=kwargs.get("insured_financial_assets", []),
        metadata=kwargs.get("metadata"),
    )


# ---------------------------------------------------------------------------
# 1. Import safety
# ---------------------------------------------------------------------------

class InsuranceAppImportSafetyTest(TestCase):
    def test_services_has_no_forbidden_imports(self):
        import ast, os
        path = os.path.join(os.path.dirname(__file__), "services.py")
        with open(path) as f:
            tree = ast.parse(f.read())
        forbidden = {"kanban", "academy", "bazaar"}
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
# 2. create_insurance_contract — archetype="insurance"
# ---------------------------------------------------------------------------

class CreateInsuranceContractTest(TestCase):
    def test_creates_contract_with_insurance_archetype(self):
        from toto.contracts.models import Contract
        contract = _make_insurance_contract()
        self.assertIsNotNone(contract.pk)
        self.assertEqual(contract.metadata["archetype"], "insurance")
        self.assertEqual(contract.status, Contract.STATUS_DRAFT)

    def test_contract_stores_premium_terms(self):
        contract = _make_insurance_contract(
            premium_amount_base_units=2_500,
            premium_frequency="quarterly",
        )
        self.assertEqual(contract.metadata["premium_amount_base_units"], 2_500)
        self.assertEqual(contract.metadata["premium_frequency"], "quarterly")

    def test_expected_core_nodes_created(self):
        contract = _make_insurance_contract()
        keys = set(contract.nodes.values_list("key", flat=True))
        self.assertSetEqual(
            keys,
            {"root", "insured_person", "payer_acct", "insurer_acct", "premium_asset"},
        )

    def test_insured_item_nodes_created(self):
        from toto.inventory.models import RealWorldObject
        item = RealWorldObject.objects.create(name="Car", estimated_value=10_000)
        contract = _make_insurance_contract(insured_items=[item])
        self.assertTrue(contract.nodes.filter(key="insured_item_1").exists())
        self.assertTrue(
            contract.edges.filter(edge_type="insures").exists()
        )


# ---------------------------------------------------------------------------
# 3. create_premium_duty — payer → insurer
# ---------------------------------------------------------------------------

class CreatePremiumDutyTest(TestCase):
    def setUp(self):
        self.asset = _make_asset("PRM")
        self.insured = _make_person("Insured P")
        self.payer_acct = _make_account("PRM-PAYER")
        self.insurer_acct = _make_account("PRM-INSURER")
        self.contract = _make_insurance_contract(
            asset=self.asset,
            insured_person=self.insured,
            payer_account=self.payer_acct,
            insurer_account=self.insurer_acct,
        )

    def test_creates_obligation_payer_to_insurer(self):
        from toto.insurance.services import create_premium_duty
        from toto.assets.models import ObligationStatus
        from toto.claims.models import ConditionKind, ConditionStatus

        duty = create_premium_duty(
            contract=self.contract,
            amount_base_units=1_000,
            due_at=timezone.now(),
        )

        ob = duty["obligation"]
        cond = duty["condition"]

        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.debtor_account, self.payer_acct)
        self.assertEqual(ob.creditor_account, self.insurer_acct)

        self.assertEqual(cond.status, ConditionStatus.PENDING)
        self.assertEqual(cond.kind, ConditionKind.APPROVAL)

    def test_premium_nodes_added_to_graph(self):
        from toto.insurance.services import create_premium_duty
        create_premium_duty(contract=self.contract, amount_base_units=500)
        self.assertTrue(
            self.contract.nodes.filter(key="premium_1", node_type="obligation").exists()
        )
        self.assertTrue(
            self.contract.nodes.filter(key="premium_approval_1", node_type="condition").exists()
        )

    def test_premium_index_increments(self):
        from toto.insurance.services import create_premium_duty
        create_premium_duty(contract=self.contract, amount_base_units=500)
        create_premium_duty(contract=self.contract, amount_base_units=500)
        self.assertTrue(self.contract.nodes.filter(key="premium_2").exists())


# ---------------------------------------------------------------------------
# 4. create_claim_duty — insurer → insured
# ---------------------------------------------------------------------------

class CreateClaimDutyTest(TestCase):
    def setUp(self):
        self.asset = _make_asset("CLM")
        self.insured = _make_person("Insured C")
        self.payer_acct = _make_account("CLM-PAYER")
        self.insurer_acct = _make_account("CLM-INSURER")
        self.contract = _make_insurance_contract(
            asset=self.asset,
            insured_person=self.insured,
            payer_account=self.payer_acct,
            insurer_account=self.insurer_acct,
        )

    def test_creates_obligation_insurer_to_insured(self):
        from toto.insurance.services import create_claim_duty
        from toto.assets.models import ObligationStatus
        from toto.claims.models import ConditionKind, ConditionStatus

        duty = create_claim_duty(
            contract=self.contract,
            amount_base_units=50_000,
            due_at=timezone.now(),
        )

        ob = duty["obligation"]
        cond = duty["condition"]

        self.assertEqual(ob.status, ObligationStatus.PENDING)
        self.assertEqual(ob.debtor_account, self.insurer_acct)
        self.assertEqual(ob.creditor_account, self.payer_acct)

        self.assertEqual(cond.status, ConditionStatus.PENDING)
        self.assertEqual(cond.kind, ConditionKind.APPROVAL)

    def test_claim_nodes_added_to_graph(self):
        from toto.insurance.services import create_claim_duty
        create_claim_duty(contract=self.contract, amount_base_units=10_000)
        self.assertTrue(
            self.contract.nodes.filter(key="claim_1", node_type="obligation").exists()
        )
        self.assertTrue(
            self.contract.nodes.filter(key="claim_approval_1", node_type="condition").exists()
        )


# ---------------------------------------------------------------------------
# 5 & 6. Settlement delegates to fulfill_obligation
# ---------------------------------------------------------------------------

class SettlePremiumTest(TestCase):
    def setUp(self):
        from toto.insurance.services import create_premium_duty, approve_premium
        self.asset = _make_asset("SPRM")
        self.insured = _make_person("Insured SP")
        self.payer_acct = _make_account("SPRM-PAYER")
        self.insurer_acct = _make_account("SPRM-INSURER")
        self.contract = _make_insurance_contract(
            asset=self.asset,
            insured_person=self.insured,
            payer_account=self.payer_acct,
            insurer_account=self.insurer_acct,
        )
        self.duty = create_premium_duty(
            contract=self.contract, amount_base_units=1_000,
        )
        _fund_account(self.payer_acct, self.asset, 50_000)
        approve_premium(condition=self.duty["condition"])

    def test_settle_premium_delegates_to_fulfill_obligation(self):
        from unittest.mock import patch
        from toto.assets.services import assets as assets_svc
        from toto.insurance.services import settle_premium
        from toto.claims.models import ContractEventKind

        with patch.object(
            assets_svc, "fulfill_obligation",
            wraps=assets_svc.fulfill_obligation,
        ) as mock_fulfill:
            result = settle_premium(
                obligation=self.duty["obligation"],
                contract=self.contract,
            )
            mock_fulfill.assert_called_once()

        self.assertEqual(result["event"].kind, ContractEventKind.SETTLEMENT)

    def test_settle_premium_marks_obligation_fulfilled(self):
        from toto.assets.models import ObligationStatus
        from toto.insurance.services import settle_premium
        settle_premium(obligation=self.duty["obligation"], contract=self.contract)
        self.duty["obligation"].refresh_from_db()
        self.assertEqual(self.duty["obligation"].status, ObligationStatus.FULFILLED)


class SettleClaimTest(TestCase):
    def setUp(self):
        from toto.insurance.services import create_claim_duty, approve_claim
        self.asset = _make_asset("SCLM")
        self.insured = _make_person("Insured SC")
        self.payer_acct = _make_account("SCLM-PAYER")
        self.insurer_acct = _make_account("SCLM-INSURER")
        self.contract = _make_insurance_contract(
            asset=self.asset,
            insured_person=self.insured,
            payer_account=self.payer_acct,
            insurer_account=self.insurer_acct,
        )
        self.duty = create_claim_duty(
            contract=self.contract, amount_base_units=10_000,
        )
        _fund_account(self.insurer_acct, self.asset, 100_000)
        approve_claim(condition=self.duty["condition"])

    def test_settle_claim_delegates_to_fulfill_obligation(self):
        from unittest.mock import patch
        from toto.assets.services import assets as assets_svc
        from toto.insurance.services import settle_claim
        from toto.claims.models import ContractEventKind

        with patch.object(
            assets_svc, "fulfill_obligation",
            wraps=assets_svc.fulfill_obligation,
        ) as mock_fulfill:
            result = settle_claim(
                obligation=self.duty["obligation"],
                contract=self.contract,
            )
            mock_fulfill.assert_called_once()

        self.assertEqual(result["event"].kind, ContractEventKind.SETTLEMENT)

    def test_settle_claim_marks_obligation_fulfilled(self):
        from toto.assets.models import ObligationStatus
        from toto.insurance.services import settle_claim
        settle_claim(obligation=self.duty["obligation"], contract=self.contract)
        self.duty["obligation"].refresh_from_db()
        self.assertEqual(self.duty["obligation"].status, ObligationStatus.FULFILLED)


# ---------------------------------------------------------------------------
# 7. Compatibility shim
# ---------------------------------------------------------------------------

class CompatibilityShimTest(TestCase):
    def test_contracts_insurance_re_exports_all_public_api(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            from toto.contracts import insurance as shim  # noqa: F401

        self.assertTrue(
            any(issubclass(w.category, DeprecationWarning) for w in caught),
            "Expected a DeprecationWarning from the shim",
        )

        from toto.insurance import services
        for name in (
            "create_insurance_contract",
            "create_premium_duty",
            "mark_premium_due",
            "approve_premium",
            "settle_premium",
            "create_claim_duty",
            "approve_claim",
            "settle_claim",
        ):
            self.assertIs(
                getattr(shim, name),
                getattr(services, name),
                f"shim.{name} should be the same object as services.{name}",
            )


# ---------------------------------------------------------------------------
# 8. Premium lifecycle
# ---------------------------------------------------------------------------

class PremiumLifecycleTest(TestCase):
    def test_full_premium_lifecycle_event_sequence(self):
        from toto.insurance.services import (
            create_premium_duty, mark_premium_due,
            approve_premium, settle_premium,
        )
        from toto.claims.models import ContractEvent, ContractEventKind

        asset = _make_asset("PLIFE")
        payer_acct = _make_account("PLIFE-PAYER")
        insurer_acct = _make_account("PLIFE-INSURER")
        _fund_account(payer_acct, asset, 100_000)

        contract = _make_insurance_contract(
            asset=asset,
            insured_person=_make_person("Insured PL"),
            payer_account=payer_acct,
            insurer_account=insurer_acct,
        )

        duty = create_premium_duty(contract=contract, amount_base_units=1_000)
        mark_premium_due(contract=contract, duty_node_key="premium_1")
        approve_premium(condition=duty["condition"])
        settle_premium(obligation=duty["obligation"], contract=contract)

        events = set(
            ContractEvent.objects.filter(
                source_type="contracts.Contract",
                source_id=str(contract.pk),
            ).values_list("kind", flat=True)
        )
        self.assertIn(ContractEventKind.OBLIGATION_CREATED, events)
        self.assertIn(ContractEventKind.PAYMENT_DUE, events)
        self.assertIn(ContractEventKind.CONDITION_SATISFIED, events)
        self.assertIn(ContractEventKind.SETTLEMENT, events)


# ---------------------------------------------------------------------------
# 9. Claim lifecycle
# ---------------------------------------------------------------------------

class ClaimLifecycleTest(TestCase):
    def test_full_claim_lifecycle_event_sequence(self):
        from toto.insurance.services import (
            create_claim_duty, approve_claim, settle_claim,
        )
        from toto.claims.models import ContractEvent, ContractEventKind

        asset = _make_asset("CLIFE")
        payer_acct = _make_account("CLIFE-PAYER")
        insurer_acct = _make_account("CLIFE-INSURER")
        _fund_account(insurer_acct, asset, 500_000)

        contract = _make_insurance_contract(
            asset=asset,
            insured_person=_make_person("Insured CL"),
            payer_account=payer_acct,
            insurer_account=insurer_acct,
        )

        duty = create_claim_duty(contract=contract, amount_base_units=50_000)
        approve_claim(condition=duty["condition"])
        settle_claim(obligation=duty["obligation"], contract=contract)

        events = set(
            ContractEvent.objects.filter(
                source_type="contracts.Contract",
                source_id=str(contract.pk),
            ).values_list("kind", flat=True)
        )
        self.assertIn(ContractEventKind.OBLIGATION_CREATED, events)
        self.assertIn(ContractEventKind.CONDITION_SATISFIED, events)
        self.assertIn(ContractEventKind.SETTLEMENT, events)
