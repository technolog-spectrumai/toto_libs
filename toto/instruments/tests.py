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
