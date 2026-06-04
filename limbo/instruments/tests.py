from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

# Use absolute imports so tests work when run as `toto.instruments` label.
from toto.instruments.models import (
    AmortizationContract,
    AmortizationEntry,
    AmortizationStatus,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    InstrumentStatus,
    InstrumentType,
)
from toto.instruments.services import AmortizationService


class InstrumentsSmokeTests(TestCase):
    def test_import_models(self):
        self.assertIsNotNone(FinancialInstrument)
        self.assertIsNotNone(EscrowContract)
        self.assertIsNotNone(ForwardContract)


def _make_amortization_contract(**kwargs):
    """Build an AmortizationContract instance without DB, bypassing FK descriptors."""
    from django.db.models.base import ModelState
    obj = object.__new__(AmortizationContract)
    obj.__dict__["_state"] = ModelState()
    for k, v in kwargs.items():
        obj.__dict__[k] = v
    return obj


class AmortizationTests(TestCase):
    """Tests for AmortizationContract model validation and service logic."""

    # ── Model validation ────────────────────────────────────────────────────

    def test_original_amount_must_be_positive(self):
        contract = _make_amortization_contract(
            original_amount_base_units=0,
            amortized_amount_base_units=0,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            contract.clean()
        self.assertIn("original_amount_base_units", ctx.exception.message_dict)

    def test_amortized_amount_cannot_be_negative(self):
        contract = _make_amortization_contract(
            original_amount_base_units=1000,
            amortized_amount_base_units=-1,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            contract.clean()
        self.assertIn("amortized_amount_base_units", ctx.exception.message_dict)

    def test_amortized_cannot_exceed_original(self):
        contract = _make_amortization_contract(
            original_amount_base_units=100,
            amortized_amount_base_units=101,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            contract.clean()
        self.assertIn("amortized_amount_base_units", ctx.exception.message_dict)

    def test_source_and_destination_must_differ(self):
        contract = _make_amortization_contract(
            original_amount_base_units=100,
            amortized_amount_base_units=0,
            source_account_id=5,
            destination_account_id=5,
            starts_at=None,
            ends_at=None,
        )
        with self.assertRaises(ValidationError) as ctx:
            contract.clean()
        self.assertIn("Source and destination accounts must differ", str(ctx.exception))

    def test_ends_at_must_be_after_starts_at(self):
        now = timezone.now()
        contract = _make_amortization_contract(
            original_amount_base_units=100,
            amortized_amount_base_units=0,
            source_account_id=1,
            destination_account_id=2,
            starts_at=now,
            ends_at=now,
        )
        with self.assertRaises(ValidationError) as ctx:
            contract.clean()
        self.assertIn("ends_at", ctx.exception.message_dict)

    def test_remaining_amount_property(self):
        contract = _make_amortization_contract(
            original_amount_base_units=1000,
            amortized_amount_base_units=300,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        self.assertEqual(contract.remaining_amount_base_units, 700)

    def test_is_exhausted_property(self):
        contract = _make_amortization_contract(
            original_amount_base_units=100,
            amortized_amount_base_units=100,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        self.assertTrue(contract.is_exhausted)

    def test_not_exhausted_when_remaining(self):
        contract = _make_amortization_contract(
            original_amount_base_units=100,
            amortized_amount_base_units=50,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        self.assertFalse(contract.is_exhausted)

    # ── Service: activate / pause / cancel ──────────────────────────────────

    @patch("toto.instruments.services.record_execution")
    def test_activate_changes_status(self, mock_record):
        instrument = SimpleNamespace(
            status=InstrumentStatus.DRAFT,
            reference="amort-001",
            save=MagicMock(),
        )
        contract = SimpleNamespace(
            status=AmortizationStatus.DRAFT,
            instrument=instrument,
            save=MagicMock(),
        )
        AmortizationService.activate.__wrapped__(contract)
        self.assertEqual(contract.status, AmortizationStatus.ACTIVE)
        self.assertEqual(instrument.status, InstrumentStatus.ACTIVE)

    @patch("toto.instruments.services.record_execution")
    def test_activate_only_from_draft(self, mock_record):
        instrument = SimpleNamespace(status=InstrumentStatus.ACTIVE, reference="x", save=MagicMock())
        contract = SimpleNamespace(status=AmortizationStatus.ACTIVE, instrument=instrument, save=MagicMock())
        with self.assertRaises(ValidationError):
            AmortizationService.activate.__wrapped__(contract)

    @patch("toto.instruments.services.record_execution")
    def test_pause_only_from_active(self, mock_record):
        instrument = SimpleNamespace(status=InstrumentStatus.DRAFT, reference="x", save=MagicMock())
        contract = SimpleNamespace(status=AmortizationStatus.DRAFT, instrument=instrument, save=MagicMock())
        with self.assertRaises(ValidationError):
            AmortizationService.pause.__wrapped__(contract)

    @patch("toto.instruments.services.record_execution")
    def test_resume_changes_status(self, mock_record):
        instrument = SimpleNamespace(status=InstrumentStatus.PAUSED, reference="x", save=MagicMock())
        contract = SimpleNamespace(status=AmortizationStatus.PAUSED, instrument=instrument, save=MagicMock())
        AmortizationService.resume.__wrapped__(contract)
        self.assertEqual(contract.status, AmortizationStatus.ACTIVE)
        self.assertEqual(instrument.status, InstrumentStatus.ACTIVE)

    @patch("toto.instruments.services.record_execution")
    def test_resume_only_from_paused(self, mock_record):
        instrument = SimpleNamespace(status=InstrumentStatus.ACTIVE, reference="x", save=MagicMock())
        contract = SimpleNamespace(status=AmortizationStatus.ACTIVE, instrument=instrument, save=MagicMock())
        with self.assertRaises(ValidationError):
            AmortizationService.resume.__wrapped__(contract)

    @patch("toto.instruments.services.record_execution")
    def test_cancel_exhausted_raises(self, mock_record):
        instrument = SimpleNamespace(status=InstrumentStatus.SETTLED, reference="x", save=MagicMock())
        contract = SimpleNamespace(
            status=AmortizationStatus.EXHAUSTED, instrument=instrument, save=MagicMock()
        )
        with self.assertRaises(ValidationError):
            AmortizationService.cancel.__wrapped__(contract)

    # ── Service: can_amortize ────────────────────────────────────────────────

    def test_can_amortize_returns_true_when_valid(self):
        contract = _make_amortization_contract(
            original_amount_base_units=1000,
            amortized_amount_base_units=200,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        contract.status = AmortizationStatus.ACTIVE
        self.assertTrue(AmortizationService.can_amortize(contract, 500))

    def test_can_amortize_false_when_exceeds_remaining(self):
        contract = _make_amortization_contract(
            original_amount_base_units=1000,
            amortized_amount_base_units=900,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        contract.status = AmortizationStatus.ACTIVE
        self.assertFalse(AmortizationService.can_amortize(contract, 200))

    def test_can_amortize_false_when_not_active(self):
        contract = _make_amortization_contract(
            original_amount_base_units=1000,
            amortized_amount_base_units=0,
            source_account_id=1,
            destination_account_id=2,
            starts_at=None,
            ends_at=None,
        )
        contract.status = AmortizationStatus.PAUSED
        self.assertFalse(AmortizationService.can_amortize(contract, 100))

    # ── Service: amortize flow ───────────────────────────────────────────────

    @patch("toto.instruments.services.AmortizationService.mark_exhausted")
    @patch("toto.instruments.services.record_execution")
    @patch("toto.instruments.services.get_backend")
    @patch("toto.instruments.services.AmortizationEntry.objects")
    def test_amortize_creates_entry_and_transfer(
        self, mock_entry_mgr, mock_backend, mock_record, mock_exhausted
    ):
        fake_tx = SimpleNamespace(reference="tx-001")
        mock_backend.return_value.transfer_asset.return_value = fake_tx
        fake_entry = SimpleNamespace(pk=42)
        mock_entry_mgr.create.return_value = fake_entry

        instrument = SimpleNamespace(reference="amort-001", status=InstrumentStatus.ACTIVE, save=MagicMock())
        asset = SimpleNamespace(decimals=2, unit_name="CRED")
        src = SimpleNamespace(code="SRC")
        dst = SimpleNamespace(code="DST")
        contract = SimpleNamespace(
            status=AmortizationStatus.ACTIVE,
            instrument=instrument,
            asset=asset,
            source_account=src,
            destination_account=dst,
            original_amount_base_units=1000,
            amortized_amount_base_units=0,
            remaining_amount_base_units=1000,
            is_exhausted=False,
            save=MagicMock(),
        )

        AmortizationService.amortize.__wrapped__(contract, 500)

        mock_backend.return_value.transfer_asset.assert_called_once()
        mock_entry_mgr.create.assert_called_once()
        self.assertEqual(contract.amortized_amount_base_units, 500)

    @patch("toto.instruments.services.record_execution")
    @patch("toto.instruments.services.get_backend")
    def test_amortize_raises_when_exceeds_remaining(self, mock_backend, mock_record):
        instrument = SimpleNamespace(reference="x", status=InstrumentStatus.ACTIVE, save=MagicMock())
        contract = SimpleNamespace(
            status=AmortizationStatus.ACTIVE,
            instrument=instrument,
            original_amount_base_units=100,
            amortized_amount_base_units=80,
            remaining_amount_base_units=20,
        )
        with self.assertRaises(ValidationError) as ctx:
            AmortizationService.amortize.__wrapped__(contract, 50)
        self.assertIn("exceeds", str(ctx.exception))

    @patch("toto.instruments.services.record_execution")
    @patch("toto.instruments.services.get_backend")
    def test_amortize_raises_when_not_active(self, mock_backend, mock_record):
        instrument = SimpleNamespace(reference="x", status=InstrumentStatus.PAUSED, save=MagicMock())
        contract = SimpleNamespace(
            status=AmortizationStatus.PAUSED,
            instrument=instrument,
            original_amount_base_units=100,
            amortized_amount_base_units=0,
            remaining_amount_base_units=100,
        )
        with self.assertRaises(ValidationError):
            AmortizationService.amortize.__wrapped__(contract, 10)

    # ── No direct AssetHolding mutation ─────────────────────────────────────

    def test_no_direct_asset_holding_mutation(self):
        """Verify AmortizationService never imports or references AssetHolding."""
        import inspect
        import toto.instruments.services as svc_module
        src = inspect.getsource(svc_module)
        self.assertNotIn("AssetHolding", src)

    # ── View auth checks ─────────────────────────────────────────────────────

    def test_amortization_list_requires_login(self):
        response = self.client.get("/instruments/amortizations/")
        self.assertEqual(response.status_code, 302)

    def test_amortization_create_requires_login(self):
        response = self.client.get("/instruments/amortizations/create/")
        self.assertEqual(response.status_code, 302)

    def test_amortization_detail_requires_login(self):
        response = self.client.get("/instruments/amortizations/1/")
        self.assertEqual(response.status_code, 302)

    def test_amortization_activate_requires_login(self):
        response = self.client.post("/instruments/amortizations/1/activate/")
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# Lapis contract generation
# ---------------------------------------------------------------------------

def _make_ledger_account(code):
    from toto.assets.models import LedgerAccount
    return LedgerAccount.objects.create(code=code, name=code, account_type="user", active=True)


def _make_asset(code, reserve):
    from decimal import Decimal
    from toto.assets.services.assets import create_asset
    return create_asset(
        name=code, unit_name=code, total_supply=Decimal("1000"),
        decimals=0, reserve_account=reserve, reference=f"create-{code.lower()}",
    )


class ForwardContractGenerationTests(TestCase):
    def setUp(self):
        from datetime import timedelta
        self.reserve = _make_ledger_account("fwd-reserve")
        self.buyer = _make_ledger_account("fwd-buyer")
        self.seller = _make_ledger_account("fwd-seller")
        self.underlying = _make_asset("FWDU", self.reserve)
        self.payment_asset = _make_asset("FWDP", self.reserve)

        from toto.instruments.models import FinancialInstrument, ForwardContract
        self.instrument = FinancialInstrument.objects.create(
            reference="FWD-LC-001", instrument_type="forward",
        )
        self.fwd = ForwardContract.objects.create(
            instrument=self.instrument,
            buyer_account=self.buyer,
            seller_account=self.seller,
            underlying_asset=self.underlying,
            quantity_base_units=100,
            payment_asset=self.payment_asset,
            payment_amount_base_units=5000,
            settlement_at=timezone.now() + timedelta(days=30),
        )

    def test_forward_generates_linked_contract(self):
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        contract = sync_contract_for_instrument(self.instrument)
        self.assertIsNotNone(contract.pk)

    def test_forward_flow_validates(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        from toto.assets.lapis.compiler import ContractFlowValidator
        code = render_lapis_for_instrument(self.instrument)
        ContractFlowValidator().validate(loads_contract(code, fmt="yaml"))

    def test_forward_lapis_has_no_oblig_node(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        code = render_lapis_for_instrument(self.instrument)
        self.assertNotIn("type: oblig", code)

    def test_forward_metadata_has_two_obligations(self):
        from toto.instruments.lapis_contracts import build_contract_metadata_for_instrument
        meta = build_contract_metadata_for_instrument(self.instrument)
        obligations = meta["obligations"]
        self.assertEqual(len(obligations), 2)
        roles = {o["role"] for o in obligations}
        self.assertIn("underlying_delivery", roles)
        self.assertIn("payment", roles)

    def test_forward_obligation_has_due_at(self):
        from toto.instruments.lapis_contracts import build_obligation_memory_for_instrument
        obligations = build_obligation_memory_for_instrument(self.instrument)
        for o in obligations:
            self.assertIn("due_at", o)

    def test_forward_metadata_instrument_reference(self):
        from toto.instruments.lapis_contracts import build_contract_metadata_for_instrument
        meta = build_contract_metadata_for_instrument(self.instrument)
        self.assertEqual(meta["instrument_reference"], "FWD-LC-001")
        self.assertEqual(meta["instrument_type"], "forward")
        self.assertEqual(meta["generated_by"], "instruments.lapis_contracts")


class InstrumentWithoutSubtypeTests(TestCase):
    def test_render_raises_for_missing_subtype(self):
        from toto.instruments.models import FinancialInstrument
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        instrument = FinancialInstrument.objects.create(
            reference="NOSUB-001", instrument_type="lease",
        )
        with self.assertRaises(ValueError):
            render_lapis_for_instrument(instrument)

    def test_amortization_pause_requires_login(self):
        response = self.client.post("/instruments/amortizations/1/pause/")
        self.assertEqual(response.status_code, 302)

    def test_amortization_cancel_requires_login(self):
        response = self.client.post("/instruments/amortizations/1/cancel/")
        self.assertEqual(response.status_code, 302)

    def test_amortization_resume_requires_login(self):
        response = self.client.post("/instruments/amortizations/1/resume/")
        self.assertEqual(response.status_code, 302)

    def test_amortize_view_requires_login(self):
        response = self.client.post("/instruments/amortizations/1/amortize/")
        self.assertEqual(response.status_code, 302)


# ---------------------------------------------------------------------------
# ingress_instruments --deploy-contracts
# ---------------------------------------------------------------------------

_DEMO_TYPES = [
    "lease",
    "amortization",
    "vesting",
    "escrow",
    "forward",
    "option",
    "staking",
    "revenue_share",
]

_FORBIDDEN_STRINGS = [
    "type: oblig",
    "type: transfer",
    "type: get_state",
    "type: set_state",
    "type: decimal",
    "type: txn",
    "type: gtxn",
    "type: inner_begin",
    "type: inner_set",
    "type: inner_submit",
    "actions:",
    "body:",
]


def _ref(itype: str) -> str:
    return f"demo-{itype.replace('_', '-')}-001"


def _run_ingress(*extra_args):
    from io import StringIO

    from django.core.management import call_command

    out = StringIO()
    call_command("ingress_instruments", *extra_args, stdout=out)
    return out.getvalue()


class IngressInstrumentsTests(TestCase):
    """Tests for ingress_instruments --deploy-contracts."""

    def test_deploy_contracts_creates_contract_for_each_type(self):
        _run_ingress("--deploy-contracts")
        for itype in _DEMO_TYPES:
            instr = FinancialInstrument.objects.get(reference=_ref(itype))
            self.assertIsNotNone(
                instr.contract_id, f"{itype}: no contract after deploy"
            )

    def test_deploy_is_idempotent(self):
        _run_ingress("--deploy-contracts")
        from toto.assets.models import Contract

        count_after_first = Contract.objects.count()
        _run_ingress("--deploy-contracts")
        self.assertEqual(Contract.objects.count(), count_after_first)

    def test_force_contracts_updates_existing_code(self):
        _run_ingress("--deploy-contracts")
        instr = FinancialInstrument.objects.select_related("contract").get(
            reference=_ref("lease")
        )
        instr.contract.code = "tampered_xyz"
        instr.contract.save(update_fields=["code"])

        _run_ingress("--force-contracts")

        instr.contract.refresh_from_db()
        self.assertNotEqual(instr.contract.code, "tampered_xyz")
        self.assertIn("language: lapis", instr.contract.code)

    def test_force_contracts_updates_metadata(self):
        _run_ingress("--deploy-contracts")
        instr = FinancialInstrument.objects.select_related("contract").get(
            reference=_ref("lease")
        )
        instr.contract.metadata = {}
        instr.contract.save(update_fields=["metadata"])

        _run_ingress("--force-contracts")

        instr.contract.refresh_from_db()
        self.assertTrue(instr.contract.metadata.get("ingress_deployed_contract"))

    def test_all_deployed_contracts_validate(self):
        from toto.assets.lapis.compiler import ContractFlowValidator
        from toto.assets.lapis.loader import loads_contract

        _run_ingress("--deploy-contracts")
        validator = ContractFlowValidator()
        for itype in _DEMO_TYPES:
            instr = FinancialInstrument.objects.select_related("contract").get(
                reference=_ref(itype)
            )
            self.assertIsNotNone(instr.contract, f"{itype}: no contract")
            tree = loads_contract(instr.contract.code, fmt="yaml")
            try:
                validator.validate(tree)
            except Exception as exc:
                self.fail(f"{itype}: Contract Flow validation failed — {exc}")

    def test_no_ledger_transaction_created_by_deploy(self):
        from toto.assets.models import LedgerTransaction

        _run_ingress()
        before = LedgerTransaction.objects.count()
        _run_ingress("--deploy-contracts")
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_no_ledger_entry_created_by_deploy(self):
        from toto.assets.models import LedgerEntry

        _run_ingress()
        before = LedgerEntry.objects.count()
        _run_ingress("--deploy-contracts")
        self.assertEqual(LedgerEntry.objects.count(), before)

    def test_revenue_share_demo_has_recipient(self):
        from toto.instruments.models import RevenueShareContract

        _run_ingress("--deploy-contracts")
        instr = FinancialInstrument.objects.get(reference=_ref("revenue_share"))
        rs = RevenueShareContract.objects.get(instrument=instr)
        self.assertGreater(rs.recipients.count(), 0)

    def test_revenue_share_deploys_successfully(self):
        _run_ingress("--deploy-contracts")
        instr = FinancialInstrument.objects.get(reference=_ref("revenue_share"))
        self.assertIsNotNone(instr.contract_id)

    def test_no_forbidden_strings_in_flow(self):
        _run_ingress("--deploy-contracts")
        for itype in _DEMO_TYPES:
            instr = FinancialInstrument.objects.select_related("contract").get(
                reference=_ref(itype)
            )
            if not instr.contract:
                continue
            code = instr.contract.code
            for bad in _FORBIDDEN_STRINGS:
                self.assertNotIn(bad, code, f"{itype}: found forbidden string '{bad}'")

    def test_ingress_metadata_markers_set(self):
        _run_ingress("--deploy-contracts")
        for itype in _DEMO_TYPES:
            instr = FinancialInstrument.objects.select_related("contract").get(
                reference=_ref(itype)
            )
            meta = instr.contract.metadata
            self.assertTrue(
                meta.get("ingress_deployed_contract"),
                f"{itype}: ingress_deployed_contract not set",
            )
            self.assertEqual(
                meta.get("ingress_source"),
                "ingress_instruments",
                f"{itype}: ingress_source wrong",
            )

    def test_without_deploy_flag_no_contracts_created(self):
        from toto.assets.models import Contract

        before = Contract.objects.count()
        _run_ingress()
        self.assertEqual(Contract.objects.count(), before)


# ---------------------------------------------------------------------------
# Instrument lifecycle sync
# ---------------------------------------------------------------------------

def _run_ingress_with_lifecycle():
    from io import StringIO
    from django.core.management import call_command
    call_command("ingress_instruments", "--sync-lifecycle", stdout=StringIO())


class InstrumentLifecycleSyncTests(TestCase):
    """Tests for sync_lifecycle_for_instrument and per-type helpers."""

    def setUp(self):
        _run_ingress_with_lifecycle()

    def _get_instr(self, ref):
        return FinancialInstrument.objects.get(reference=ref)

    def test_lease_creates_entitlement_schedule_condition(self):
        from toto.claims.models import Entitlement, Schedule, Condition
        instr = self._get_instr("demo-lease-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Entitlement.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Schedule.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Condition.objects.filter(**src).count(), 1)

    def test_amortization_creates_entitlement_and_allocation(self):
        from toto.claims.models import Entitlement, Allocation
        instr = self._get_instr("demo-amortization-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Entitlement.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Allocation.objects.filter(**src).count(), 1)

    def test_vesting_creates_all_four_primitives(self):
        from toto.claims.models import Entitlement, Allocation, Schedule, Condition
        instr = self._get_instr("demo-vesting-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Entitlement.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Allocation.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Schedule.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Condition.objects.filter(**src).count(), 1)

    def test_escrow_creates_allocation_and_condition(self):
        from toto.claims.models import Allocation, Condition
        instr = self._get_instr("demo-escrow-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Allocation.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Condition.objects.filter(**src).count(), 1)

    def test_forward_creates_schedule_and_condition(self):
        from toto.claims.models import Schedule, Condition
        instr = self._get_instr("demo-forward-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Schedule.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Condition.objects.filter(**src).count(), 1)

    def test_option_creates_entitlement_schedule_condition(self):
        from toto.claims.models import Entitlement, Schedule, Condition
        instr = self._get_instr("demo-option-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Entitlement.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Schedule.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Condition.objects.filter(**src).count(), 1)

    def test_staking_creates_entitlement_allocation_schedule(self):
        from toto.claims.models import Entitlement, Allocation, Schedule
        instr = self._get_instr("demo-staking-001")
        src = {"source_type": "instruments.FinancialInstrument", "source_id": str(instr.pk)}
        self.assertGreaterEqual(Entitlement.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Allocation.objects.filter(**src).count(), 1)
        self.assertGreaterEqual(Schedule.objects.filter(**src).count(), 1)

    def test_revenue_share_creates_entitlements_per_recipient(self):
        from toto.claims.models import Entitlement
        instr = self._get_instr("demo-revenue-share-001")
        # Revenue share creates one entitlement per recipient with compound source_id
        ents = Entitlement.objects.filter(
            source_type="instruments.FinancialInstrument",
            source_id__startswith=str(instr.pk) + ":",
        )
        self.assertGreaterEqual(ents.count(), 2)

    def test_idempotency_all_instruments(self):
        """Running sync twice creates no duplicate records."""
        from toto.claims.models import Entitlement, Schedule, Condition, Allocation, ContractEvent
        before = {
            "ents": Entitlement.objects.count(),
            "scheds": Schedule.objects.count(),
            "conds": Condition.objects.count(),
            "allocs": Allocation.objects.count(),
            "events": ContractEvent.objects.count(),
        }
        _run_ingress_with_lifecycle()
        self.assertEqual(Entitlement.objects.count(), before["ents"])
        self.assertEqual(Schedule.objects.count(), before["scheds"])
        self.assertEqual(Condition.objects.count(), before["conds"])
        self.assertEqual(Allocation.objects.count(), before["allocs"])
        self.assertEqual(ContractEvent.objects.count(), before["events"])

    def test_no_ledger_transactions_created_by_lifecycle(self):
        from toto.assets.models import LedgerTransaction
        before = LedgerTransaction.objects.count()
        _run_ingress_with_lifecycle()
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_created_events_exist_for_each_instrument(self):
        from toto.claims.models import ContractEvent, ContractEventKind
        refs = [
            "demo-lease-001", "demo-amortization-001",
            "demo-vesting-001", "demo-escrow-001", "demo-forward-001",
            "demo-option-001", "demo-staking-001", "demo-revenue-share-001",
        ]
        for ref in refs:
            instr = FinancialInstrument.objects.get(reference=ref)
            exists = ContractEvent.objects.filter(
                source_type="instruments.FinancialInstrument",
                source_id=str(instr.pk),
                kind=ContractEventKind.CREATED,
            ).exists()
            self.assertTrue(exists, f"No CREATED event for {ref}")

    def test_ingress_sync_lifecycle_flag(self):
        from io import StringIO
        from django.core.management import call_command
        from toto.claims.models import Schedule
        before = Schedule.objects.count()
        call_command("ingress_instruments", "--sync-lifecycle", stdout=StringIO())
        # Second run is idempotent — count should not increase
        self.assertEqual(Schedule.objects.count(), before)
