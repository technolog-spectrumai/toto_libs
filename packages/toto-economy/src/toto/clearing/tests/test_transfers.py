"""Stage-3 suite: the signed bridge and the transfer protocol.

Both platforms live in ONE database here, distinguished by their peer rows and
account codes — enough to exercise sequencing, signatures, replay and the
accounting directions. The full two-database harness arrives in stage 6.
"""

from __future__ import annotations

import uuid as uuid_lib
from decimal import Decimal

from django.test import override_settings
from toto.assets.testing import LedgerTestCase as TestCase

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.assets.queries import get_asset_balance, verify_asset_ledger
from toto.assets.services.assets import create_asset, distribute_asset

from toto.clearing.models import (
    ClearingHold,
    ClearingInbox,
    ClearingOutbox,
    ClearingTransfer,
    LedgerPeer,
    SharedAsset,
)
from toto.clearing.services import bridge, transfers
from toto.clearing.services import trustline as trustline_service
from toto.clearing.tests.test_peer import FERNET_KEY, make_peer


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class BridgeTests(TestCase):
    """Sequencing, signatures and the replay shield, without any value."""

    def setUp(self):
        # "us" signs; "them" is the row on the receiving side that has our key
        # pinned. One database, two viewpoints.
        self.us = make_peer(platform_id="us", client_id="us",
                            status=LedgerPeer.STATUS_ACTIVE)
        self.us.ensure_keypair()
        self.us.save()
        self.them = make_peer(platform_id="them", client_id="them",
                              status=LedgerPeer.STATUS_ACTIVE,
                              peer_public_key_pem=self.us.our_public_key_pem)

    def _send(self, kind="transfer.prepare", **payload):
        row = bridge.enqueue(self.us, kind, payload or {"n": 1})
        return row.envelope()

    def test_seq_is_monotonic_per_peer(self):
        first = self._send(n=1)
        second = self._send(n=2)
        self.assertEqual(second["seq"], first["seq"] + 1)
        self.us.refresh_from_db()
        self.assertEqual(self.us.send_seq, second["seq"])

    def test_applied_once_and_replay_returns_stored_response(self):
        calls = []

        def apply_fn(peer, kind, payload):
            calls.append(payload)
            return {"status": "ok", "echo": payload.get("n")}

        envelope = self._send(n=7)
        first = bridge.receive(self.them, envelope, apply_fn)
        again = bridge.receive(self.them, envelope, apply_fn)
        self.assertEqual(first, again)
        self.assertEqual(len(calls), 1, "a replay must not re-apply")

    def test_same_id_different_params_is_refused(self):
        # Note the shape: a payload edited in flight fails on the SIGNATURE
        # (see the next test). Reaching the idempotency check therefore takes a
        # properly signed second message that reuses the first one's id — the
        # sender-bug case the Stripe rule exists for.
        first = self._send(n=1)
        bridge.receive(self.them, first, lambda p, k, pl: {"status": "ok"})
        second = self._send(n=2)
        second["uuid"] = first["uuid"]
        second["seq"] = first["seq"]
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            bridge.receive(self.them, second, lambda p, k, pl: {"status": "ok"})
        self.assertEqual(ctx.exception.status, "idempotency-conflict")

    def test_edited_payload_fails_on_the_signature_first(self):
        envelope = self._send(n=1)
        bridge.receive(self.them, envelope, lambda p, k, pl: {"status": "ok"})
        tampered = dict(envelope, payload={"n": 2})
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            bridge.receive(self.them, tampered, lambda p, k, pl: {"status": "ok"})
        self.assertEqual(ctx.exception.status, "bad-signature")

    def test_bad_signature_refused(self):
        envelope = self._send(n=1)
        envelope["signature"] = envelope["signature"][:-4] + "AAAA"
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            bridge.receive(self.them, envelope, lambda p, k, pl: {"status": "ok"})
        self.assertEqual(ctx.exception.status, "bad-signature")

    def test_sequence_gap_refused(self):
        self._send(n=1)
        second = self._send(n=2)  # never delivered
        third = self._send(n=3)
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            bridge.receive(self.them, third, lambda p, k, pl: {"status": "ok"})
        self.assertEqual(ctx.exception.status, "gap")

    def test_refusal_is_recorded_not_retried(self):
        def refuse(peer, kind, payload):
            raise bridge.InboxRefusal("nope", status="rejected")

        envelope = self._send(n=1)
        response = bridge.receive(self.them, envelope, refuse)
        self.assertEqual(response["status"], "rejected")
        row = ClearingInbox.objects.get(peer=self.them, uuid=envelope["uuid"])
        self.assertEqual(row.state, ClearingInbox.REJECTED)
        # The sender's retry sees the same answer.
        self.assertEqual(bridge.receive(self.them, envelope, refuse), response)


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class TransferProtocolTests(TestCase):
    """One platform's ledger, driven through both roles of the protocol."""

    def setUp(self):
        self.peer = make_peer(platform_id="peer", client_id="link",
                              status=LedgerPeer.STATUS_ACTIVE)
        self.peer.ensure_keypair()
        self.peer.save()
        self.peer.peer_public_key_pem = self.peer.our_public_key_pem
        self.peer.save()

        self.reserve = LedgerAccount.objects.create(
            code="RES3", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_asset(
            name="Bridge Token", unit_name="BRT", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-brt")
        self.asset.reserve_account = self.reserve
        self.asset.save(update_fields=["reserve_account"])
        self.alice = LedgerAccount.objects.create(
            code="ALICE3", name="Alice", account_type=AccountType.USER)
        self.bob = LedgerAccount.objects.create(
            code="BOB3", name="Bob", account_type=AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=self.alice,
                         amount=Decimal("100"), reference="fund-alice3")
        self.line = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal("500"))

    def _base(self, amount):
        return to_base_units(Decimal(amount), self.asset.decimals)

    def test_send_holds_value_and_queues_a_signed_prepare(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))

        self.assertEqual(record.state, ClearingTransfer.PREPARED)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("90"))
        self.assertEqual(
            get_asset_balance(self.asset, self.line.escrow_account), self._base("10"))
        row = ClearingOutbox.objects.get(peer=self.peer, kind="transfer.prepare")
        self.assertEqual(row.state, ClearingOutbox.QUEUED)
        self.assertTrue(row.signature)

    def test_sender_hold_outlives_the_receiver_deadline(self):
        # The in-doubt rule, structurally: the receiver always decides first.
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        self.assertGreater(record.hold.expires_at, record.deadline)

    def test_home_side_cannot_credit_more_than_it_ever_sent(self):
        # The issuer-homed invariant: inbound value comes OUT of the peer's
        # position account, so a peer cannot return what it never received.
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            transfers.apply_prepare(self.peer, {
                "transfer_uuid": str(uuid_lib.uuid4()),
                "unit_name": "BRT",
                "amount_base_units": self._base("10"),
                "receiver_account_code": "BOB3",
                "sender_account_code": "REMOTE-ALICE",
            })
        self.assertEqual(ctx.exception.status, "rejected")

    def test_receiver_credits_and_signs_a_receipt(self):
        # Fund the peer's position first — i.e. we sent value out earlier, and
        # this is some of it coming back.
        outbound = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("50"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(outbound.uuid)})

        transfer_uuid = str(uuid_lib.uuid4())
        response = transfers.apply_prepare(self.peer, {
            "transfer_uuid": transfer_uuid,
            "unit_name": "BRT",
            "amount_base_units": self._base("10"),
            "receiver_account_code": "BOB3",
            "sender_account_code": "REMOTE-ALICE",
        })
        self.assertEqual(response["status"], "fulfilled")
        self.assertTrue(response["receipt_signature"])
        self.assertEqual(get_asset_balance(self.asset, self.bob), self._base("10"))
        # The credited row carries the portable origin identity.
        record = ClearingTransfer.objects.get(uuid=transfer_uuid)
        record.ledger_txn.refresh_from_db()
        self.assertEqual(record.ledger_txn.origin_platform, "peer")
        self.assertEqual(str(record.ledger_txn.origin_uuid), transfer_uuid)

    def test_private_asset_never_crosses(self):
        # An asset with no trustline for this peer is refused, and the refusal
        # is a SIGNED reject the sender can verify.
        private_reserve = LedgerAccount.objects.create(
            code="PRIV-RES", name="Private reserve", account_type=AccountType.RESERVE)
        create_asset(name="Private", unit_name="PRV", total_supply=Decimal("10"),
                     decimals=2, reserve_account=private_reserve, reference="mk-prv")
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            transfers.apply_prepare(self.peer, {
                "transfer_uuid": str(uuid_lib.uuid4()),
                "unit_name": "PRV",
                "amount_base_units": self._base("1"),
                "receiver_account_code": "BOB3",
            })
        self.assertEqual(ctx.exception.status, "no-trustline")
        self.assertTrue(
            ClearingOutbox.objects.filter(kind="transfer.reject").exists())

    def test_fulfill_posts_the_hold(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(record.uuid)})
        record.refresh_from_db()
        record.hold.refresh_from_db()
        self.assertEqual(record.state, ClearingTransfer.FULFILLED)
        self.assertEqual(record.hold.state, ClearingHold.POSTED)
        # Value left escrow for the peer's position account.
        self.assertEqual(
            get_asset_balance(self.asset, self.line.escrow_account), 0)
        self.assertEqual(
            get_asset_balance(self.asset, self.line.vostro_account), self._base("10"))

    def test_reject_voids_the_hold_and_refunds(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        transfers.apply_reject(self.peer, {"transfer_uuid": str(record.uuid),
                                           "reason": "unknown account"})
        record.refresh_from_db()
        self.assertEqual(record.state, ClearingTransfer.REJECTED)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_fulfill_is_idempotent(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(record.uuid)})
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(record.uuid)})
        self.assertEqual(
            get_asset_balance(self.asset, self.line.vostro_account), self._base("10"))

    def test_credit_limit_refuses_the_send(self):
        from django.core.exceptions import ValidationError

        tight = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal("500"))
        SharedAsset.objects.filter(pk=tight.pk).update(
            max_owed_base_units=self._base("5"))
        tight.refresh_from_db()
        with self.assertRaises(ValidationError):
            transfers.send_to_peer(
                peer=self.peer, shared=tight, origin_account=self.alice,
                remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        # Nothing moved.
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_ledger_invariants_survive_the_protocol(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB", amount_base=self._base("10"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(record.uuid)})
        transfers.apply_prepare(self.peer, {
            "transfer_uuid": str(uuid_lib.uuid4()),
            "unit_name": "BRT",
            "amount_base_units": self._base("4"),
            "receiver_account_code": "BOB3",
            "sender_account_code": "REMOTE-ALICE",
        })
        report = verify_asset_ledger(self.asset)
        self.assertTrue(report["entries_balanced"])
        self.assertTrue(report["total_supply_matches"])


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class InboxEndpointTests(TestCase):
    """The machine surface: auth, application, and the receipt relayed back."""

    def setUp(self):
        self.peer = make_peer(platform_id="ep-peer", client_id="link-1",
                              status=LedgerPeer.STATUS_ACTIVE)
        self.peer.ensure_keypair()
        self.peer.save()
        # One database, so the row that signs is also the row that verifies.
        LedgerPeer.objects.filter(pk=self.peer.pk).update(
            peer_public_key_pem=self.peer.our_public_key_pem)
        self.peer.refresh_from_db()

        self.reserve = LedgerAccount.objects.create(
            code="RES4", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_asset(
            name="Endpoint Token", unit_name="EPT", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-ept")
        self.asset.reserve_account = self.reserve
        self.asset.save(update_fields=["reserve_account"])
        self.bob = LedgerAccount.objects.create(
            code="BOB4", name="Bob", account_type=AccountType.USER)
        # Mirror side: credits are issued from the reserve, so no prior
        # position is needed — this is the child receiving from its parent.
        self.line = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=False,
            max_owed=Decimal("500"))

    def _basic(self):
        import base64

        token = base64.b64encode(b"link-1:s3cret").decode()
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def _prepare_envelope(self, amount="10", unit="EPT"):
        import json as _json

        row = bridge.enqueue(self.peer, "transfer.prepare", {
            "transfer_uuid": str(uuid_lib.uuid4()),
            "unit_name": unit,
            "amount_base_units": to_base_units(Decimal(amount), 2),
            "receiver_account_code": "BOB4",
            "sender_account_code": "REMOTE-ALICE",
        })
        return _json.dumps(row.envelope())

    def test_unauthenticated_is_refused(self):
        resp = self.client.post("/clearing/api/inbox/", data=self._prepare_envelope(),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_prepare_credits_and_returns_a_signed_receipt(self):
        resp = self.client.post("/clearing/api/inbox/", data=self._prepare_envelope(),
                                content_type="application/json", **self._basic())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "fulfilled")
        self.assertEqual(body["reply"]["kind"], "transfer.fulfill")
        self.assertTrue(body["reply"]["signature"])
        self.assertEqual(get_asset_balance(self.asset, self.bob),
                         to_base_units(Decimal("10"), 2))

    def test_replayed_post_credits_once(self):
        envelope = self._prepare_envelope()
        first = self.client.post("/clearing/api/inbox/", data=envelope,
                                 content_type="application/json", **self._basic())
        second = self.client.post("/clearing/api/inbox/", data=envelope,
                                  content_type="application/json", **self._basic())
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(get_asset_balance(self.asset, self.bob),
                         to_base_units(Decimal("10"), 2))
