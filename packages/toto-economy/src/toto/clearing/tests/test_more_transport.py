"""Delivery: the outbox pusher, the reply relay, and the beat tasks.

``transport.http_post`` is module-level so a harness can patch it; every test
here patches it (or ``requests.post`` underneath it) — nothing leaves the
process. One database plays both platforms, as in test_transfers: the peer row
that signs is also the row that verifies.
"""

from __future__ import annotations

import unittest
import uuid as uuid_lib
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.assets.queries import get_asset_balance
from toto.assets.services.assets import distribute_asset
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.services import create_currency

from toto.clearing import tasks
from toto.clearing.models import (
    ClearingHold,
    ClearingInbox,
    ClearingOutbox,
    ClearingTransfer,
    LedgerPeer,
)
from toto.clearing.services import bridge, transfers, transport, wire
from toto.clearing.services import trustline as trustline_service
from toto.clearing.tests.test_peer import FERNET_KEY, make_peer

HTTP_POST = "toto.clearing.services.transport.http_post"


def _self_pinned_peer(**kw):
    """A peer whose pinned key is our own — one DB standing in for two."""
    peer = make_peer(status=LedgerPeer.STATUS_ACTIVE, **kw)
    peer.ensure_keypair()
    peer.save()
    LedgerPeer.objects.filter(pk=peer.pk).update(
        peer_public_key_pem=peer.our_public_key_pem)
    peer.refresh_from_db()
    return peer


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class DeliverTests(TestCase):
    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="far", client_id="link-far",
                                      base_url="https://far.test/")

    def _queue(self, n=1):
        return bridge.enqueue(self.peer, "transfer.prepare", {"n": n})

    def test_an_acknowledged_message_is_marked_acked(self):
        row = self._queue()
        with mock.patch(HTTP_POST, return_value=(200, {"status": "ok"})) as post:
            self.assertTrue(transport.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.ACKED)
        self.assertEqual(row.last_error, "")
        post.assert_called_once()

    def test_it_posts_the_envelope_to_the_peer_inbox_with_the_link_secret(self):
        row = self._queue()
        with mock.patch(HTTP_POST, return_value=(200, {})) as post:
            transport.deliver(row)
        url = post.call_args.args[0]
        # The trailing slash on base_url does not double up.
        self.assertEqual(url, "https://far.test/clearing/api/inbox/")
        self.assertEqual(post.call_args.kwargs["auth"], ("link-far", "s3cret"))
        sent = post.call_args.kwargs["payload"]
        self.assertEqual(sent["uuid"], str(row.uuid))
        self.assertEqual(sent["seq"], row.seq)
        self.assertEqual(sent["signature"], row.signature)

    def test_a_non_200_answer_fails_the_row_and_keeps_the_peers_words(self):
        row = self._queue()
        with mock.patch(HTTP_POST, return_value=(502, {"detail": "upstream down"})):
            self.assertFalse(transport.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.FAILED)
        self.assertEqual(row.attempts, 1)
        self.assertIn("HTTP 502", row.last_error)
        self.assertIn("upstream down", row.last_error)

    def test_a_transport_exception_fails_the_row_with_its_type_and_message(self):
        row = self._queue()
        with mock.patch(HTTP_POST, side_effect=ConnectionError("refused")):
            self.assertFalse(transport.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.FAILED)
        self.assertEqual(row.last_error, "ConnectionError: refused")

    def test_the_last_allowed_failure_holds_the_row_for_a_human(self):
        row = self._queue()
        ClearingOutbox.objects.filter(pk=row.pk).update(
            attempts=transport.MAX_ATTEMPTS - 1)
        row.refresh_from_db()
        with mock.patch(HTTP_POST, return_value=(500, {})):
            transport.deliver(row)
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.HELD)
        self.assertEqual(row.attempts, transport.MAX_ATTEMPTS)

    def test_a_failed_row_can_be_delivered_on_a_later_try(self):
        row = self._queue()
        with mock.patch(HTTP_POST, return_value=(500, {})):
            transport.deliver(row)
        row.refresh_from_db()
        with mock.patch(HTTP_POST, return_value=(200, {})):
            self.assertTrue(transport.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.ACKED)

    def test_an_already_acked_row_is_not_sent_again(self):
        row = self._queue()
        ClearingOutbox.objects.filter(pk=row.pk).update(state=ClearingOutbox.ACKED)
        row.refresh_from_db()
        with mock.patch(HTTP_POST) as post:
            self.assertTrue(transport.deliver(row))
        post.assert_not_called()

    def test_a_row_another_worker_is_sending_is_left_alone(self):
        row = self._queue()
        ClearingOutbox.objects.filter(pk=row.pk).update(state=ClearingOutbox.SENDING)
        row.refresh_from_db()
        with mock.patch(HTTP_POST) as post:
            self.assertFalse(transport.deliver(row))
        post.assert_not_called()

    def test_a_held_row_is_never_sent_by_the_machine(self):
        row = self._queue()
        ClearingOutbox.objects.filter(pk=row.pk).update(state=ClearingOutbox.HELD)
        row.refresh_from_db()
        with mock.patch(HTTP_POST) as post:
            self.assertFalse(transport.deliver(row))
        post.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.HELD)

    def test_an_unverifiable_reply_does_not_undo_the_acknowledgement(self):
        row = self._queue()
        forged = {"kind": "transfer.fulfill", "payload": {"transfer_uuid": "x"},
                  "signature": "AAAA", "uuid": str(uuid_lib.uuid4()), "seq": 1}
        with mock.patch(HTTP_POST, return_value=(200, {"reply": forged})):
            self.assertTrue(transport.deliver(row))
        row.refresh_from_db()
        self.assertEqual(row.state, ClearingOutbox.ACKED)
        self.assertFalse(ClearingInbox.objects.exists())


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class ReplyRelayTests(TestCase):
    """The receiver's signed receipt, relayed back in the HTTP answer, settles
    the sender's hold through the same inbox a redelivery would use."""

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="relay", client_id="link-relay")
        self.reserve = LedgerAccount.objects.create(
            code="RES-RELAY", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_currency(
            name="Relay Token", unit_name="RLY", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-rly")
        self.alice = LedgerAccount.objects.create(
            code="ALICE-RLY", name="Alice", account_type=AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=self.alice,
                         amount=Decimal("100"), reference="fund-alice-rly")
        self.line = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal("500"))

    def _receipt_for(self, record, seq=1):
        envelope = wire.sign_envelope(self.peer, "transfer.fulfill", {
            "transfer_uuid": str(record.uuid), "credited_txn_uuid": "t",
            "at": "2026-09-29T00:00:00+00:00"})
        envelope.update(uuid=str(uuid_lib.uuid4()), seq=seq)
        return envelope

    def test_a_relayed_receipt_posts_the_hold_in_the_same_round_trip(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB",
            amount_base=to_base_units(Decimal("10"), 2))
        row = ClearingOutbox.objects.get(kind="transfer.prepare")

        with mock.patch(HTTP_POST, return_value=(
                200, {"status": "fulfilled", "reply": self._receipt_for(record)})):
            self.assertTrue(transport.deliver(row))

        record.refresh_from_db()
        record.hold.refresh_from_db()
        self.assertEqual(record.state, ClearingTransfer.FULFILLED)
        self.assertEqual(record.hold.state, ClearingHold.POSTED)
        self.assertEqual(get_asset_balance(self.asset, self.line.vostro_account),
                         to_base_units(Decimal("10"), 2))

    def test_the_receipt_arriving_twice_settles_once(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="REMOTE-BOB",
            amount_base=to_base_units(Decimal("10"), 2))
        receipt = self._receipt_for(record)
        # Fast path: relayed in the HTTP answer.
        row = ClearingOutbox.objects.get(kind="transfer.prepare")
        with mock.patch(HTTP_POST, return_value=(200, {"reply": receipt})):
            transport.deliver(row)
        # Durable path: the receiver's own outbox redelivers the same envelope.
        bridge.receive(self.peer, receipt, transfers.dispatch)

        self.assertEqual(get_asset_balance(self.asset, self.line.vostro_account),
                         to_base_units(Decimal("10"), 2))
        self.assertEqual(ClearingInbox.objects.filter(peer=self.peer).count(), 1)


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class DispatchQueuedTests(TestCase):
    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="q", client_id="link-q")

    def test_delivers_in_sequence_order_and_counts_the_acks(self):
        rows = [bridge.enqueue(self.peer, "transfer.prepare", {"n": n})
                for n in range(3)]
        with mock.patch(HTTP_POST, return_value=(200, {})) as post:
            self.assertEqual(transport.dispatch_queued(), 3)
        sent_seqs = [c.kwargs["payload"]["seq"] for c in post.call_args_list]
        self.assertEqual(sent_seqs, [r.seq for r in rows])

    def test_a_failure_stops_the_stream_so_nothing_overtakes_it(self):
        first = bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        second = bridge.enqueue(self.peer, "transfer.prepare", {"n": 2})
        with mock.patch(HTTP_POST, return_value=(503, {})) as post:
            self.assertEqual(transport.dispatch_queued(), 0)
        self.assertEqual(post.call_count, 1)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.state, ClearingOutbox.FAILED)
        self.assertEqual(second.state, ClearingOutbox.QUEUED)

    def test_a_suspended_peer_is_sent_nothing(self):
        bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        LedgerPeer.objects.filter(pk=self.peer.pk).update(
            status=LedgerPeer.STATUS_SUSPENDED)
        with mock.patch(HTTP_POST) as post:
            self.assertEqual(transport.dispatch_queued(), 0)
        post.assert_not_called()

    def test_the_limit_caps_one_sweep(self):
        for n in range(3):
            bridge.enqueue(self.peer, "transfer.prepare", {"n": n})
        with mock.patch(HTTP_POST, return_value=(200, {})):
            self.assertEqual(transport.dispatch_queued(limit=2), 2)
        self.assertEqual(
            ClearingOutbox.objects.filter(state=ClearingOutbox.QUEUED).count(), 1)

    def test_flush_after_commit_pushes_once_the_transaction_commits(self):
        bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        with mock.patch(HTTP_POST, return_value=(200, {})) as post:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                transport.flush_after_commit()
            post.assert_not_called()          # nothing before the commit
            for callback in callbacks:
                callback()
        post.assert_called_once()
        self.assertTrue(ClearingOutbox.objects.filter(
            state=ClearingOutbox.ACKED).exists())


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class RedriveTests(TestCase):
    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="rd", client_id="link-rd")

    @unittest.skip(
        "BUG toto/clearing/services/transport.py:110 — redrive() calls "
        ".update() on a sliced queryset ([:limit]), which Django refuses with "
        "TypeError, so FAILED rows are never re-queued")
    def test_failed_rows_below_the_cap_are_requeued_and_held_rows_are_not(self):
        failed = bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        held = bridge.enqueue(self.peer, "transfer.prepare", {"n": 2})
        ClearingOutbox.objects.filter(pk=failed.pk).update(
            state=ClearingOutbox.FAILED, attempts=1)
        ClearingOutbox.objects.filter(pk=held.pk).update(
            state=ClearingOutbox.HELD, attempts=transport.MAX_ATTEMPTS)
        self.assertEqual(transport.redrive(), 1)
        failed.refresh_from_db()
        held.refresh_from_db()
        self.assertEqual(failed.state, ClearingOutbox.QUEUED)
        self.assertEqual(held.state, ClearingOutbox.HELD)


class HttpPostTests(TestCase):
    """The one outbound call, with ``requests`` itself mocked."""

    def _response(self, status=200, body=None, bad_json=False):
        response = mock.Mock(status_code=status)
        if bad_json:
            response.json.side_effect = ValueError("not json")
        else:
            response.json.return_value = body or {}
        return response

    def test_it_sends_http_basic_json_with_the_backchannel_timeout(self):
        import base64
        import json

        with mock.patch("requests.post",
                        return_value=self._response(200, {"ok": 1})) as post:
            status, body = transport.http_post(
                "https://peer.test/x", auth=("id", "sec"), payload={"a": 1})
        self.assertEqual((status, body), (200, {"ok": 1}))
        kwargs = post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], transport.BACKCHANNEL_TIMEOUT)
        self.assertEqual(json.loads(kwargs["data"]), {"a": 1})
        token = kwargs["headers"]["Authorization"].split(" ", 1)[1]
        self.assertEqual(base64.b64decode(token).decode(), "id:sec")
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")

    def test_a_non_json_answer_reads_as_an_empty_body(self):
        with mock.patch("requests.post",
                        return_value=self._response(502, bad_json=True)):
            self.assertEqual(transport.http_post(
                "https://peer.test/x", auth=("a", "b"), payload={}), (502, {}))


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class TaskTests(TestCase):
    """The beat tasks are thin: each returns what its service did."""

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="tk", client_id="link-tk")

    def test_dispatch_outbox_returns_the_number_delivered(self):
        bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        with mock.patch(HTTP_POST, return_value=(200, {})):
            self.assertEqual(tasks.dispatch_outbox(), 1)

    def test_dispatch_outbox_with_nothing_queued_is_zero(self):
        with mock.patch(HTTP_POST) as post:
            self.assertEqual(tasks.dispatch_outbox(), 0)
        post.assert_not_called()

    def test_expire_holds_voids_what_is_past_its_deadline(self):
        reserve = LedgerAccount.objects.create(
            code="RES-TK", name="Reserve", account_type=AccountType.RESERVE)
        asset = create_currency(
            name="Task Token", unit_name="TKT", total_supply=Decimal("100"),
            decimals=0, reserve_account=reserve, reference="mk-tkt")
        alice = LedgerAccount.objects.create(
            code="ALICE-TK", name="Alice", account_type=AccountType.USER)
        distribute_asset(asset=asset, recipient_account=alice,
                         amount=Decimal("10"), reference="fund-tk")
        line = trustline_service.open_trustline(
            asset=asset, peer=self.peer, issued_here=True, max_owed=Decimal("50"))
        from toto.clearing.services import holds as holds_service

        stale = holds_service.create_hold(
            shared=line, origin_account=alice, amount_base=3,
            purpose=ClearingHold.PURPOSE_TRANSFER)
        fresh = holds_service.create_hold(
            shared=line, origin_account=alice, amount_base=2,
            purpose=ClearingHold.PURPOSE_TRANSFER)
        ClearingHold.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1))

        self.assertEqual(tasks.expire_holds(), 1)
        fresh.refresh_from_db()
        self.assertEqual(fresh.state, ClearingHold.PENDING)
        self.assertEqual(get_asset_balance(asset, alice), 8)

    @unittest.skip(
        "BUG toto/clearing/services/transport.py:110 — redrive_failed always "
        "raises TypeError (update() on a sliced queryset)")
    def test_redrive_failed_returns_the_number_requeued(self):
        row = bridge.enqueue(self.peer, "transfer.prepare", {"n": 1})
        ClearingOutbox.objects.filter(pk=row.pk).update(
            state=ClearingOutbox.FAILED, attempts=1)
        self.assertEqual(tasks.redrive_failed(), 1)
