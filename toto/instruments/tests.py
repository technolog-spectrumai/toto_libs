from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

# Use absolute imports so tests work when run as `toto.instruments` label.
from toto.instruments.models import (
    BillingMode,
    ChargeStatus,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    InstrumentStatus,
    InstrumentType,
    LeaseCharge,
    LeaseContract,
    LeaseMetric,
    LeaseStatus,
    LeaseTariff,
    RoundingMode,
)
from toto.instruments.services import LeaseService


class InstrumentsSmokeTests(TestCase):
    def test_import_models(self):
        self.assertIsNotNone(FinancialInstrument)
        self.assertIsNotNone(EscrowContract)
        self.assertIsNotNone(ForwardContract)


def _make_lease_contract(**kwargs):
    """Build a LeaseContract instance without DB, bypassing FK descriptors."""
    obj = object.__new__(LeaseContract)
    # Django Model.__init__ sets _state; replicate it minimally.
    from django.db.models.base import ModelState
    obj.__dict__["_state"] = ModelState()
    for k, v in kwargs.items():
        obj.__dict__[k] = v
    return obj


class LeaseTests(TestCase):
    """Tests for Lease instrument models, service logic, and views."""

    # ── Model validation ────────────────────────────────────────────────────

    def test_lease_metric_step_must_be_positive(self):
        metric = LeaseMetric(code="test", name="Test", kind="usage", unit="req", step=Decimal("0"))
        with self.assertRaises(ValidationError) as ctx:
            metric.clean()
        self.assertIn("step", ctx.exception.message_dict)

    def test_lease_metric_negative_step_invalid(self):
        metric = LeaseMetric(code="test", name="Test", kind="usage", unit="req", step=Decimal("-1"))
        with self.assertRaises(ValidationError) as ctx:
            metric.clean()
        self.assertIn("step", ctx.exception.message_dict)

    def test_fixed_billing_requires_positive_fee(self):
        """Fixed billing mode with zero fee should fail clean()."""
        lease = _make_lease_contract(
            billing_mode=BillingMode.FIXED,
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
            billing_mode=BillingMode.METERED,
            fixed_fee_base_units=0,
            starts_at=timezone.now(),
            ends_at=None,
            lessor_account_id=5,
            lessee_account_id=5,  # same!
        )
        with self.assertRaises(ValidationError) as ctx:
            lease.clean()
        self.assertIn("Lessor and lessee accounts must differ", str(ctx.exception))

    def test_ends_at_must_be_after_starts_at(self):
        now = timezone.now()
        lease = _make_lease_contract(
            billing_mode=BillingMode.METERED,
            fixed_fee_base_units=0,
            starts_at=now,
            ends_at=now,  # same, not after
            lessor_account_id=1,
            lessee_account_id=2,
        )
        with self.assertRaises(ValidationError) as ctx:
            lease.clean()
        self.assertIn("ends_at", ctx.exception.message_dict)

    # ── calculate_metered_charge ─────────────────────────────────────────────

    @staticmethod
    def _make_tariff(step, price, minimum=0, rounding=None):
        if rounding is None:
            rounding = RoundingMode.ROUND_UP_STEP
        metric = SimpleNamespace(step=Decimal(str(step)))
        tariff = SimpleNamespace(
            metric=metric,
            rounding_mode=rounding,
            price_per_step_base_units=price,
            minimum_charge_base_units=minimum,
        )
        return tariff

    def test_calculate_metered_charge_round_up(self):
        """743 tokens with step=1000, ROUND_UP → 1 step billed."""
        tariff = self._make_tariff(1000, 500)
        billed_qty, billed_steps, amount = LeaseService.calculate_metered_charge(
            tariff, Decimal("743")
        )
        self.assertEqual(billed_steps, Decimal("1"))
        self.assertEqual(billed_qty, Decimal("1000"))
        self.assertEqual(amount, 500)

    def test_calculate_metered_charge_exact_step(self):
        """1000 tokens with step=1000, ROUND_UP → exactly 1 step."""
        tariff = self._make_tariff(1000, 500)
        billed_qty, billed_steps, amount = LeaseService.calculate_metered_charge(
            tariff, Decimal("1000")
        )
        self.assertEqual(billed_steps, Decimal("1"))
        self.assertEqual(amount, 500)

    def test_calculate_metered_charge_minimum_applied(self):
        """When computed amount < minimum, minimum_charge is used."""
        tariff = self._make_tariff(1, 10, minimum=100)
        _, _, amount = LeaseService.calculate_metered_charge(tariff, Decimal("1"))
        self.assertEqual(amount, 100)

    def test_calculate_metered_charge_round_down(self):
        """743 tokens with step=1000, ROUND_DOWN → 0 steps billed."""
        tariff = self._make_tariff(1000, 500, rounding=RoundingMode.ROUND_DOWN_STEP)
        _, billed_steps, amount = LeaseService.calculate_metered_charge(
            tariff, Decimal("743")
        )
        self.assertEqual(int(billed_steps), 0)
        self.assertEqual(amount, 0)

    # ── Service activate ─────────────────────────────────────────────────────

    @patch("toto.instruments.services.record_execution")
    def test_service_activate_changes_status(self, mock_record):
        """LeaseService.activate() updates status to active and saves."""
        instrument = SimpleNamespace(
            status=InstrumentStatus.DRAFT,
            reference="test-lease",
            save=MagicMock(),
        )
        now = timezone.now()
        lease = SimpleNamespace(
            status=LeaseStatus.DRAFT,
            starts_at=now,
            instrument=instrument,
            save=MagicMock(),
        )

        LeaseService.activate.__wrapped__(lease)  # bypass @transaction.atomic

        self.assertEqual(lease.status, LeaseStatus.ACTIVE)
        self.assertIsNotNone(lease.activated_at)
        self.assertEqual(instrument.status, InstrumentStatus.ACTIVE)

    # ── View auth checks ─────────────────────────────────────────────────────

    def test_lease_list_requires_login(self):
        response = self.client.get("/instruments/leases/")
        self.assertEqual(response.status_code, 302)

    def test_lease_create_requires_login(self):
        response = self.client.get("/instruments/leases/create/")
        self.assertEqual(response.status_code, 302)

    def test_lease_metric_create_requires_login(self):
        response = self.client.get("/instruments/leases/metrics/create/")
        self.assertEqual(response.status_code, 302)
