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
    LeaseContract,
    LeaseStatus,
)
from toto.instruments.services import AmortizationService, LeaseService


class InstrumentsSmokeTests(TestCase):
    def test_import_models(self):
        self.assertIsNotNone(FinancialInstrument)
        self.assertIsNotNone(EscrowContract)
        self.assertIsNotNone(ForwardContract)


def _make_lease_contract(**kwargs):
    from django.db.models.base import ModelState
    obj = object.__new__(LeaseContract)
    obj.__dict__["_state"] = ModelState()
    for k, v in kwargs.items():
        obj.__dict__[k] = v
    return obj


class LeaseTests(TestCase):
    """Tests for Lease instrument models and service logic."""

    def test_fee_must_be_positive(self):
        lease = _make_lease_contract(
            fixed_fee_base_units=0,
            starts_at=timezone.now(),
            ends_at=None,
            lessor_account_id=1,
            lessee_account_id=2,
        )
        with self.assertRaises(ValidationError) as ctx:
            lease.clean()
        self.assertIn("fixed_fee_base_units", ctx.exception.message_dict)

    def test_lessor_lessee_must_differ(self):
        lease = _make_lease_contract(
            fixed_fee_base_units=100,
            starts_at=timezone.now(),
            ends_at=None,
            lessor_account_id=5,
            lessee_account_id=5,
        )
        with self.assertRaises(ValidationError) as ctx:
            lease.clean()
        self.assertIn("Lessor and lessee accounts must differ", str(ctx.exception))

    def test_ends_at_must_be_after_starts_at(self):
        now = timezone.now()
        lease = _make_lease_contract(
            fixed_fee_base_units=100,
            starts_at=now,
            ends_at=now,
            lessor_account_id=1,
            lessee_account_id=2,
        )
        with self.assertRaises(ValidationError) as ctx:
            lease.clean()
        self.assertIn("ends_at", ctx.exception.message_dict)

    @patch("toto.instruments.services.record_execution")
    def test_service_activate_changes_status(self, mock_record):
        instrument = SimpleNamespace(
            status=InstrumentStatus.DRAFT,
            reference="test-lease",
            save=MagicMock(),
        )
        lease = SimpleNamespace(
            status=LeaseStatus.DRAFT,
            starts_at=timezone.now(),
            instrument=instrument,
            save=MagicMock(),
        )
        LeaseService.activate.__wrapped__(lease)
        self.assertEqual(lease.status, LeaseStatus.ACTIVE)
        self.assertIsNotNone(lease.activated_at)
        self.assertEqual(instrument.status, InstrumentStatus.ACTIVE)

    def test_lease_list_requires_login(self):
        response = self.client.get("/instruments/leases/")
        self.assertEqual(response.status_code, 302)

    def test_lease_create_requires_login(self):
        response = self.client.get("/instruments/leases/create/")
        self.assertEqual(response.status_code, 302)


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


class SubscriptionContractGenerationTests(TestCase):
    def setUp(self):
        self.reserve = _make_ledger_account("lc-reserve")
        self.subscriber = _make_ledger_account("lc-subscriber")
        self.provider = _make_ledger_account("lc-provider")
        self.asset = _make_asset("LCT", self.reserve)

        from toto.instruments.models import FinancialInstrument, SubscriptionContract
        from datetime import timedelta
        now = timezone.now()
        self.instrument = FinancialInstrument.objects.create(
            reference="SUB-LC-001", instrument_type="subscription",
        )
        self.sub = SubscriptionContract.objects.create(
            instrument=self.instrument,
            subscriber_account=self.subscriber,
            provider_account=self.provider,
            asset=self.asset,
            amount_base_units=500,
            billing_cycle="monthly",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            next_billing_at=now + timedelta(days=30),
        )

    def test_sync_creates_linked_contract(self):
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        contract = sync_contract_for_instrument(self.instrument)
        self.instrument.refresh_from_db()
        self.assertIsNotNone(contract.pk)
        self.assertEqual(self.instrument.contract_id, contract.pk)

    def test_generated_lapis_validates(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        from toto.assets.lapis.compiler import LapisCompiler
        code = render_lapis_for_instrument(self.instrument)
        tree = loads_contract(code, fmt="yaml")
        LapisCompiler().validate_contract(tree)

    def test_generated_lapis_has_body_key_on_all_actions(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        code = render_lapis_for_instrument(self.instrument)
        tree = loads_contract(code, fmt="yaml")
        for name, action in tree["actions"].items():
            self.assertIn("body", action, f"Action '{name}' missing body")

    def test_generated_lapis_has_no_macro_nodes(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        code = render_lapis_for_instrument(self.instrument)
        for bad in ("type: oblig", "type: transfer", "type: record",
                    "type: account", "type: asset", "type: amount", "type: balance",
                    "type: get_state", "type: set_state", "type: decimal"):
            self.assertNotIn(bad, code, f"Found forbidden node: {bad}")

    def test_generated_lapis_has_bill_period_action(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        code = render_lapis_for_instrument(self.instrument)
        tree = loads_contract(code, fmt="yaml")
        self.assertIn("bill_period", tree["actions"])

    def test_metadata_has_obligation_memory(self):
        from toto.instruments.lapis_contracts import build_contract_metadata_for_instrument
        meta = build_contract_metadata_for_instrument(self.instrument)
        self.assertIn("obligations", meta)
        self.assertEqual(len(meta["obligations"]), 1)
        self.assertEqual(meta["obligations"][0]["role"], "recurring_payment")
        self.assertEqual(meta["obligations"][0]["amount_base_units"], 500)

    def test_sync_is_idempotent(self):
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        c1 = sync_contract_for_instrument(self.instrument)
        c2 = sync_contract_for_instrument(self.instrument)
        self.assertEqual(c1.pk, c2.pk)

    def test_sync_does_not_create_ledger_transaction(self):
        from toto.assets.models import LedgerTransaction
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        before = LedgerTransaction.objects.count()
        sync_contract_for_instrument(self.instrument)
        self.assertEqual(LedgerTransaction.objects.count(), before)

    def test_sync_does_not_create_ledger_entry(self):
        from toto.assets.models import LedgerEntry
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        before = LedgerEntry.objects.count()
        sync_contract_for_instrument(self.instrument)
        self.assertEqual(LedgerEntry.objects.count(), before)

    def test_build_contract_is_noop_if_already_linked(self):
        from toto.instruments.lapis_contracts import build_contract_for_instrument, sync_contract_for_instrument
        c1 = sync_contract_for_instrument(self.instrument)
        c2 = build_contract_for_instrument(self.instrument)
        self.assertEqual(c1.pk, c2.pk)


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

    def test_forward_lapis_validates(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        from toto.assets.lapis.compiler import LapisCompiler
        code = render_lapis_for_instrument(self.instrument)
        LapisCompiler().validate_contract(loads_contract(code, fmt="yaml"))

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


class LeaseContractGenerationTests(TestCase):
    def setUp(self):
        self.reserve = _make_ledger_account("ls-reserve")
        self.lessor = _make_ledger_account("ls-lessor")
        self.lessee = _make_ledger_account("ls-lessee")
        self.revenue_acc = _make_ledger_account("ls-revenue")
        self.leased_asset = _make_asset("LSA", self.reserve)
        self.payment_asset = _make_asset("LSP", self.reserve)

        from toto.instruments.models import FinancialInstrument, LeaseContract
        self.instrument = FinancialInstrument.objects.create(
            reference="LSE-LC-001", instrument_type="lease",
        )
        self.lease = LeaseContract.objects.create(
            instrument=self.instrument,
            lessor_account=self.lessor,
            lessee_account=self.lessee,
            leased_asset=self.leased_asset,
            payment_asset=self.payment_asset,
            revenue_account=self.revenue_acc,
            fixed_fee_base_units=200,
            billing_period="monthly",
            starts_at=timezone.now(),
        )

    def test_lease_generates_contract(self):
        from toto.instruments.lapis_contracts import sync_contract_for_instrument
        contract = sync_contract_for_instrument(self.instrument)
        self.assertIsNotNone(contract.pk)

    def test_lease_lapis_validates(self):
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        from toto.assets.lapis.loader import loads_contract
        from toto.assets.lapis.compiler import LapisCompiler
        code = render_lapis_for_instrument(self.instrument)
        LapisCompiler().validate_contract(loads_contract(code, fmt="yaml"))

    def test_lease_metadata_has_obligation(self):
        from toto.instruments.lapis_contracts import build_contract_metadata_for_instrument
        meta = build_contract_metadata_for_instrument(self.instrument)
        self.assertEqual(len(meta["obligations"]), 1)
        self.assertEqual(meta["obligations"][0]["role"], "recurring_payment")


class InstrumentWithoutSubtypeTests(TestCase):
    def test_render_raises_for_missing_subtype(self):
        from toto.instruments.models import FinancialInstrument
        from toto.instruments.lapis_contracts import render_lapis_for_instrument
        instrument = FinancialInstrument.objects.create(
            reference="NOSUB-001", instrument_type="subscription",
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
