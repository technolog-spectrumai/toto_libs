"""Stage-2 suite: trustlines, credit limits, and the escrow-hold lifecycle."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.assets.queries import get_asset_balance, verify_asset_ledger
from toto.assets.services.assets import create_asset, distribute_asset

from toto.clearing.models import ClearingHold, LedgerPeer, SharedAsset
from toto.clearing.services import holds as holds_service
from toto.clearing.services import trustline as trustline_service
from toto.clearing.tests.test_peer import FERNET_KEY, make_peer


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class TrustlineTests(TestCase):
    def setUp(self):
        self.peer = make_peer(status=LedgerPeer.STATUS_ACTIVE)
        self.reserve = LedgerAccount.objects.create(
            code="RES", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_asset(
            name="Shared Token", unit_name="SHT", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-sht")
        self.asset.reserve_account = self.reserve
        self.asset.save(update_fields=["reserve_account"])
        self.alice = LedgerAccount.objects.create(
            code="ALICE", name="Alice", account_type=AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=self.alice,
                         amount=Decimal("100"), reference="fund-alice")

    def _line(self, max_owed="50"):
        return trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal(max_owed))

    def test_open_creates_system_accounts_once(self):
        line = self._line()
        again = self._line()
        self.assertEqual(line.pk, again.pk)
        self.assertEqual(line.escrow_account.account_type, AccountType.SYSTEM)
        self.assertEqual(line.vostro_account.account_type, AccountType.EXTERNAL)

    def test_mirror_refuses_overloading_a_private_ticker(self):
        # SHT exists locally and is NOT shared with this peer under a mirror
        # trustline — ensure_mirror_asset must refuse to reuse the name.
        other = make_peer(platform_id="other-peer", client_id="q",
                          status=LedgerPeer.STATUS_ACTIVE)
        with self.assertRaises(ValidationError):
            trustline_service.ensure_mirror_asset(
                unit_name="SHT", decimals=2, peer=other, max_owed=Decimal("10"))

    def test_mirror_creates_same_ticker_with_reserve(self):
        shared = trustline_service.ensure_mirror_asset(
            unit_name="RMT", decimals=2, peer=self.peer, max_owed=Decimal("10"))
        self.assertFalse(shared.issued_here)
        self.assertEqual(shared.asset.unit_name, "RMT")
        self.assertEqual(trustline_service.net_position_base(shared), 0)

    def test_credit_limit_enforced(self):
        line = self._line(max_owed="50")
        with self.assertRaises(ValidationError):
            trustline_service.check_credit(
                line, to_base_units(Decimal("51"), self.asset.decimals))
        trustline_service.check_credit(
            line, to_base_units(Decimal("50"), self.asset.decimals))


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class HoldLifecycleTests(TestCase):
    def setUp(self):
        self.peer = make_peer(status=LedgerPeer.STATUS_ACTIVE)
        self.reserve = LedgerAccount.objects.create(
            code="RES2", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_asset(
            name="Hold Token", unit_name="HLT", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-hlt")
        self.asset.reserve_account = self.reserve
        self.asset.save(update_fields=["reserve_account"])
        self.alice = LedgerAccount.objects.create(
            code="ALICE2", name="Alice", account_type=AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=self.alice,
                         amount=Decimal("100"), reference="fund-alice2")
        self.line = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal("500"))

    def _base(self, amount):
        return to_base_units(Decimal(amount), self.asset.decimals)

    def _hold(self, amount="10"):
        return holds_service.create_hold(
            shared=self.line, origin_account=self.alice,
            amount_base=self._base(amount),
            purpose=ClearingHold.PURPOSE_TRANSFER)

    def test_hold_moves_value_into_escrow(self):
        hold = self._hold("10")
        self.assertEqual(hold.state, ClearingHold.PENDING)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("90"))
        self.assertEqual(
            get_asset_balance(self.asset, self.line.escrow_account), self._base("10"))
        self.assertEqual(trustline_service.pending_escrow_base(self.line),
                         self._base("10"))

    def test_create_hold_is_idempotent_by_uuid(self):
        hold = self._hold("10")
        again = holds_service.create_hold(
            shared=self.line, origin_account=self.alice,
            amount_base=self._base("10"),
            purpose=ClearingHold.PURPOSE_TRANSFER, hold_uuid=hold.uuid)
        self.assertEqual(hold.pk, again.pk)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("90"))

    def test_post_settles_to_destination_once(self):
        hold = self._hold("10")
        posted = holds_service.post_hold(hold, destination=self.line.vostro_account)
        self.assertEqual(posted.state, ClearingHold.POSTED)
        self.assertEqual(
            get_asset_balance(self.asset, self.line.vostro_account), self._base("10"))
        # Replayed decision: returns the same outcome, moves nothing.
        again = holds_service.post_hold(hold, destination=self.line.vostro_account)
        self.assertEqual(again.state, ClearingHold.POSTED)
        self.assertEqual(
            get_asset_balance(self.asset, self.line.vostro_account), self._base("10"))
        self.assertEqual(
            get_asset_balance(self.asset, self.line.escrow_account), 0)

    def test_void_refunds_origin(self):
        hold = self._hold("10")
        voided = holds_service.void_hold(hold)
        self.assertEqual(voided.state, ClearingHold.VOIDED)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_posted_hold_cannot_be_voided(self):
        hold = self._hold("10")
        holds_service.post_hold(hold, destination=self.line.vostro_account)
        with self.assertRaises(ValidationError):
            holds_service.void_hold(hold)

    def test_sweeper_expires_stale_holds(self):
        hold = self._hold("10")
        ClearingHold.objects.filter(pk=hold.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))
        count = holds_service.expire_stale_holds()
        self.assertEqual(count, 1)
        hold.refresh_from_db()
        self.assertEqual(hold.state, ClearingHold.EXPIRED)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_ledger_invariants_hold_through_the_lifecycle(self):
        h1 = self._hold("10")
        h2 = self._hold("5")
        holds_service.post_hold(h1, destination=self.line.vostro_account)
        holds_service.void_hold(h2)
        report = verify_asset_ledger(self.asset)
        self.assertTrue(report["entries_balanced"])
        self.assertTrue(report["total_supply_matches"])