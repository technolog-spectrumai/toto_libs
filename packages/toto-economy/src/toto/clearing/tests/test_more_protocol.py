"""The trust anchor, the wire, the bridge and the transfer protocol — the
refusals and edges the stage suites leave out.

One database plays both platforms (see test_transfers): a peer row whose
pinned key is its own public key can both sign and verify.
"""

from __future__ import annotations

import base64
import json
import uuid as uuid_lib
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from toto.assets.models import AccountType, LedgerAccount, to_base_units
from toto.assets.queries import get_asset_balance, verify_asset_ledger
from toto.assets.services.assets import distribute_asset
from toto.assets.testing import LedgerTestCase as TestCase
from toto.mint.services import create_currency

from toto.clearing.admin import LedgerPeerAdmin
from toto.clearing.models import (
    ClearingHold,
    ClearingInbox,
    ClearingOutbox,
    ClearingTransfer,
    LedgerPeer,
    SharedAsset,
)
from toto.clearing.services import bridge, handshake as hs, holds as holds_service
from toto.clearing.services import transfers, wire
from toto.clearing.services import trustline as trustline_service
from toto.clearing.tests.test_peer import FERNET_KEY, make_peer


def _self_pinned_peer(**kw):
    peer = make_peer(status=LedgerPeer.STATUS_ACTIVE, **kw)
    peer.ensure_keypair()
    peer.save()
    LedgerPeer.objects.filter(pk=peer.pk).update(
        peer_public_key_pem=peer.our_public_key_pem)
    peer.refresh_from_db()
    return peer


def _basic(client_id="link-1", secret="s3cret"):
    token = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


# ── The peer row ─────────────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class PeerRowTests(TestCase):
    def test_a_peer_with_no_secret_yet_reads_as_empty_and_accepts_nothing(self):
        peer = LedgerPeer.objects.create(
            name="bare", base_url="https://bare.test", role=LedgerPeer.ROLE_CHILD,
            platform_id="bare", client_id="bare")
        self.assertEqual(peer.get_secret(), "")
        self.assertFalse(peer.check_secret(""))
        self.assertFalse(peer.check_secret("anything"))

    def test_a_second_rotation_retires_the_first_secret(self):
        peer = make_peer()
        peer.set_secret("two")
        peer.set_secret("three")
        peer.save()
        self.assertTrue(peer.check_secret("three"))
        self.assertTrue(peer.check_secret("two"))
        self.assertFalse(peer.check_secret("s3cret"))

    def test_the_keypair_is_generated_once_and_then_kept(self):
        peer = make_peer()
        peer.ensure_keypair()
        first_public = peer.our_public_key_pem
        first_sealed = bytes(peer.our_private_key_encrypted)
        peer.ensure_keypair()
        self.assertEqual(peer.our_public_key_pem, first_public)
        self.assertEqual(bytes(peer.our_private_key_encrypted), first_sealed)
        self.assertNotIn(b"PRIVATE KEY", first_sealed)

    def test_nothing_verifies_before_a_key_is_pinned(self):
        signer = make_peer(platform_id="signer", client_id="s")
        signature = signer.sign(b"hello")
        unpinned = make_peer(platform_id="unpinned", client_id="u")
        self.assertFalse(unpinned.verify_peer(b"hello", signature))

    def test_a_corrupted_pinned_key_refuses_rather_than_crashes(self):
        peer = make_peer(peer_public_key_pem="-----BEGIN PUBLIC KEY-----\nnope\n")
        self.assertFalse(peer.verify_peer(b"hello", "AAAA"))

    def test_a_signature_that_is_not_base64_refuses(self):
        peer = _self_pinned_peer(platform_id="b64", client_id="b64")
        self.assertFalse(peer.verify_peer(b"hello", "***not base64***"))

    def test_the_pinned_key_verifies_its_own_signature_only_over_the_same_bytes(self):
        peer = _self_pinned_peer(platform_id="own", client_id="own")
        signature = peer.sign(b"exact")
        self.assertTrue(peer.verify_peer(b"exact", signature))
        self.assertFalse(peer.verify_peer(b"exact!", signature))

    def test_str_names_role_and_status(self):
        peer = make_peer(name="parent-host", role=LedgerPeer.ROLE_PARENT)
        self.assertEqual(str(peer), "parent-host (parent, pending)")

    def test_the_admin_can_never_delete_a_peer(self):
        admin = LedgerPeerAdmin(LedgerPeer, AdminSite())
        self.assertFalse(admin.has_delete_permission(request=None))
        self.assertFalse(admin.has_delete_permission(request=None, obj=make_peer()))


# ── The wire ─────────────────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class WireFormatTests(TestCase):
    def test_canonical_json_ignores_key_order_and_whitespace(self):
        self.assertEqual(wire.canonical({"b": 1, "a": [1, 2]}),
                         wire.canonical({"a": [1, 2], "b": 1}))
        self.assertEqual(wire.canonical({"b": 1, "a": 2}), b'{"a":2,"b":1}')

    def test_the_frame_carries_the_context_and_a_big_endian_length(self):
        frame = wire.framed("statement", b"abc")
        self.assertTrue(frame.startswith(b"toto/clearing/v1/statement\x00"))
        self.assertTrue(frame.endswith(b"\x00\x00\x00\x03abc"))

    def test_an_unknown_kind_cannot_even_be_framed(self):
        with self.assertRaises(KeyError):
            wire.framed("transfer.steal", b"{}")

    def test_the_payload_hash_is_bound_to_the_kind(self):
        payload = {"transfer_uuid": "x"}
        self.assertNotEqual(wire.payload_hash("transfer.fulfill", payload),
                            wire.payload_hash("transfer.reject", payload))
        self.assertEqual(wire.payload_hash("transfer.fulfill", payload),
                         wire.payload_hash("transfer.fulfill", dict(payload)))

    def test_every_envelope_names_the_sending_platform(self):
        peer = make_peer()
        envelope = wire.sign_envelope(peer, "checkpoint", {"root": "r"})
        self.assertEqual(envelope["platform_id"], "clearing-suite")
        self.assertEqual(envelope["payload_hash"],
                         wire.payload_hash("checkpoint", {"root": "r"}))

    @override_settings(CLEARING_SELF_PLATFORM_ID="", PLATFORM_DOMAIN="host.example")
    def test_without_an_explicit_id_the_platform_domain_names_us(self):
        self.assertEqual(wire._self_platform_id(), "host.example")

    def test_an_envelope_without_a_signature_does_not_verify(self):
        peer = _self_pinned_peer(platform_id="nosig", client_id="nosig")
        envelope = wire.sign_envelope(peer, "statement", {"x": 1})
        del envelope["signature"]
        self.assertFalse(wire.verify_envelope(peer, envelope))


# ── The bridge ───────────────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class BridgeEdgeTests(TestCase):
    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="edge", client_id="edge")

    def _envelope(self, **payload):
        return bridge.enqueue(self.peer, "transfer.prepare", payload or {"n": 1}).envelope()

    def test_the_outbox_row_verifies_as_what_it_claims_to_be(self):
        envelope = self._envelope(n=5)
        self.assertEqual(envelope["kind"], "transfer.prepare")
        self.assertTrue(wire.verify_envelope(self.peer, envelope))
        self.assertEqual(envelope["payload_hash"],
                         wire.payload_hash("transfer.prepare", {"n": 5}))

    def test_a_message_without_uuid_or_seq_is_refused(self):
        envelope = self._envelope()
        for missing in ("uuid", "seq"):
            broken = {k: v for k, v in envelope.items() if k != missing}
            with self.assertRaises(bridge.InboxRefusal) as ctx:
                bridge.receive(self.peer, broken, lambda p, k, pl: {"status": "ok"})
            self.assertEqual(ctx.exception.status, "refused")
        self.assertFalse(ClearingInbox.objects.exists())

    def test_a_crash_in_the_handler_leaves_no_trace_so_the_retry_applies(self):
        envelope = self._envelope()

        def explode(peer, kind, payload):
            raise RuntimeError("database hiccup")

        with self.assertRaises(RuntimeError):
            bridge.receive(self.peer, envelope, explode)
        self.assertFalse(ClearingInbox.objects.exists())
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.recv_seq, 0)

        response = bridge.receive(self.peer, envelope,
                                  lambda p, k, pl: {"status": "ok"})
        self.assertEqual(response, {"status": "ok"})
        self.assertEqual(ClearingInbox.objects.get().state, ClearingInbox.APPLIED)

    def test_the_receive_counter_never_moves_backwards(self):
        first = self._envelope(n=1)
        second = self._envelope(n=2)
        bridge.receive(self.peer, first, lambda p, k, pl: {"status": "ok"})
        bridge.receive(self.peer, second, lambda p, k, pl: {"status": "ok"})
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.recv_seq, 2)
        # A late, never-seen message carrying an old number applies, but the
        # stream position stays where it was.
        late = self._envelope(n=3)
        late["seq"] = 1
        bridge.receive(self.peer, late, lambda p, k, pl: {"status": "ok"})
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.recv_seq, 2)

    def test_a_refusal_carries_its_reason(self):
        refusal = bridge.InboxRefusal("because", status="no")
        self.assertEqual((refusal.reason, refusal.status, str(refusal)),
                         ("because", "no", "because"))


# ── Handshake ────────────────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class HandshakeEdgeTests(TestCase):
    def setUp(self):
        self.caller = make_peer(platform_id="caller-row", client_id="caller")
        self.peer = make_peer(platform_id="placeholder", client_id="link-1")

    def test_first_contact_adopts_the_platform_id_the_peer_announces(self):
        hs.apply_hello(self.peer, hs.build_hello(self.caller))
        self.peer.refresh_from_db()
        self.assertEqual(self.peer.platform_id, "clearing-suite")
        self.assertEqual(self.peer.peer_epoch, str(self.caller.epoch))

    def test_the_hello_is_signed_and_carries_our_key_and_epoch(self):
        hello = hs.build_hello(self.caller)
        self.assertEqual(hello["kind"], "handshake")
        self.assertEqual(hello["payload"]["public_key_pem"],
                         self.caller.our_public_key_pem)
        self.assertEqual(hello["payload"]["epoch"], str(self.caller.epoch))
        # The keypair was persisted, so the next hello signs with the same key.
        self.caller.refresh_from_db()
        self.assertEqual(hs.build_hello(self.caller)["payload"]["public_key_pem"],
                         hello["payload"]["public_key_pem"])

    def test_a_repeat_hello_from_the_pinned_peer_reactivates_it(self):
        hs.apply_hello(self.peer, hs.build_hello(self.caller))
        LedgerPeer.objects.filter(pk=self.peer.pk).update(
            status=LedgerPeer.STATUS_PENDING)
        self.peer.refresh_from_db()
        result = hs.apply_hello(self.peer, hs.build_hello(self.caller))
        self.peer.refresh_from_db()
        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(self.peer.status, LedgerPeer.STATUS_ACTIVE)

    def test_a_hello_the_pinned_key_did_not_sign_is_refused_and_changes_nothing(self):
        hs.apply_hello(self.peer, hs.build_hello(self.caller))
        impostor = make_peer(platform_id="impostor", client_id="imp")
        result = hs.apply_hello(self.peer, hs.build_hello(impostor))
        self.peer.refresh_from_db()
        self.assertEqual(result, {"status": "bad-signature"})
        self.assertEqual(self.peer.status, LedgerPeer.STATUS_ACTIVE)
        self.assertEqual(self.peer.peer_public_key_pem,
                         self.caller.our_public_key_pem)

    def test_a_signed_announcement_of_a_new_key_suspends_and_never_repins(self):
        hs.apply_hello(self.peer, hs.build_hello(self.caller))
        other = make_peer(platform_id="other-key", client_id="ok")
        other.ensure_keypair()
        hello = hs.build_hello(self.caller)
        payload = dict(hello["payload"], public_key_pem=other.our_public_key_pem)
        resigned = wire.sign_envelope(self.caller, "handshake", payload)

        result = hs.apply_hello(self.peer, resigned)
        self.peer.refresh_from_db()
        self.assertEqual(result["status"], "suspended-epoch-or-key-change")
        self.assertEqual(self.peer.status, LedgerPeer.STATUS_SUSPENDED)
        self.assertEqual(self.peer.peer_public_key_pem,
                         self.caller.our_public_key_pem)


# ── The machine endpoints ────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class EndpointAuthTests(TestCase):
    URL = "/clearing/api/inbox/"

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="auth-peer", client_id="link-1")

    def _post(self, url=None, data="{}", **headers):
        return self.client.post(url or self.URL, data=data,
                                content_type="application/json", **headers)

    def test_a_wrong_secret_is_401_with_a_basic_challenge(self):
        response = self._post(**_basic(secret="guess"))
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], 'Basic realm="clearing"')

    def test_an_unknown_client_is_401(self):
        self.assertEqual(self._post(**_basic(client_id="nobody")).status_code, 401)

    def test_a_non_basic_scheme_is_401(self):
        response = self._post(HTTP_AUTHORIZATION="Bearer s3cret")
        self.assertEqual(response.status_code, 401)

    def test_credentials_without_a_secret_are_401(self):
        token = base64.b64encode(b"link-1").decode()
        response = self._post(HTTP_AUTHORIZATION=f"Basic {token}")
        self.assertEqual(response.status_code, 401)

    def test_undecodable_credentials_are_401_not_500(self):
        token = base64.b64encode(b"\xff\xfe:\xfd").decode()
        self.assertEqual(self._post(HTTP_AUTHORIZATION=f"Basic {token}").status_code, 401)
        self.assertEqual(self._post(HTTP_AUTHORIZATION="Basic a").status_code, 401)

    def test_both_endpoints_refuse_get(self):
        for url in (self.URL, "/clearing/api/handshake/"):
            self.assertEqual(self.client.get(url, **_basic()).status_code, 405)

    def test_malformed_json_is_400_on_both_endpoints(self):
        for url in (self.URL, "/clearing/api/handshake/"):
            response = self._post(url=url, data="{not json", **_basic())
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {"error": "invalid json"})


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class HandshakeEndpointTests(TestCase):
    URL = "/clearing/api/handshake/"

    def setUp(self):
        self.caller = make_peer(platform_id="hs-caller", client_id="hs-caller")
        self.peer = make_peer(platform_id="hs-peer", client_id="link-1")

    def _post(self, envelope):
        return self.client.post(self.URL, data=json.dumps(envelope),
                                content_type="application/json", **_basic())

    def test_a_forged_hello_after_pinning_is_403_without_our_hello(self):
        self.assertEqual(self._post(hs.build_hello(self.caller)).status_code, 200)
        impostor = make_peer(platform_id="hs-imp", client_id="hs-imp")
        response = self._post(hs.build_hello(impostor))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"status": "bad-signature"})

    def test_the_reply_hello_verifies_against_our_key(self):
        body = self._post(hs.build_hello(self.caller)).json()
        self.peer.refresh_from_db()
        hello = body["hello"]
        self.assertEqual(hello["payload"]["public_key_pem"],
                         self.peer.our_public_key_pem)
        # The caller pins it the same way, and the signature then holds.
        self.caller.peer_public_key_pem = self.peer.our_public_key_pem
        self.assertTrue(wire.verify_envelope(self.caller, hello))


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class InboxEndpointRefusalTests(TestCase):
    URL = "/clearing/api/inbox/"

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="ib-peer", client_id="link-1")
        self.reserve = LedgerAccount.objects.create(
            code="RES-IB", name="Reserve", account_type=AccountType.RESERVE)
        self.asset = create_currency(
            name="Inbox Token", unit_name="IBT", total_supply=Decimal("1000"),
            decimals=2, reserve_account=self.reserve, reference="mk-ibt")
        self.bob = LedgerAccount.objects.create(
            code="BOB-IB", name="Bob", account_type=AccountType.USER)
        trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=False,
            max_owed=Decimal("500"))

    def _prepare(self, unit="IBT", account="BOB-IB", amount="10"):
        return bridge.enqueue(self.peer, "transfer.prepare", {
            "transfer_uuid": str(uuid_lib.uuid4()), "unit_name": unit,
            "amount_base_units": to_base_units(Decimal(amount), 2),
            "receiver_account_code": account,
            "sender_account_code": "REMOTE-ALICE",
        }).envelope()

    def _post(self, envelope):
        return self.client.post(self.URL, data=json.dumps(envelope),
                                content_type="application/json", **_basic())

    def test_a_bad_signature_is_403_and_nothing_is_recorded(self):
        envelope = self._prepare()
        envelope["payload"]["amount_base_units"] = 10 ** 9
        response = self._post(envelope)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["status"], "bad-signature")
        self.assertNotIn("reply", response.json())
        self.assertEqual(get_asset_balance(self.asset, self.bob), 0)

    def test_a_reused_id_with_new_parameters_is_409(self):
        first = self._prepare(amount="1")
        self.assertEqual(self._post(first).status_code, 200)
        second = self._prepare(amount="2")
        second["uuid"], second["seq"] = first["uuid"], first["seq"]
        response = self._post(second)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["status"], "idempotency-conflict")
        self.assertEqual(get_asset_balance(self.asset, self.bob), 100)

    def test_a_sequence_gap_is_400_and_applies_nothing(self):
        self._prepare()                 # seq 1, never delivered
        response = self._post(self._prepare())
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["status"], "gap")
        self.assertEqual(get_asset_balance(self.asset, self.bob), 0)

    def test_a_refused_prepare_answers_with_a_signed_reject_sent_only_once(self):
        response = self._post(self._prepare(account="NOBODY"))
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unknown-account")
        self.assertEqual(body["reply"]["kind"], "transfer.reject")
        self.assertTrue(wire.verify_envelope(self.peer, body["reply"]))
        # Handed over in the answer, so the sweeper will not send it again.
        reject = ClearingOutbox.objects.get(kind="transfer.reject")
        self.assertEqual(reject.state, ClearingOutbox.ACKED)

    def test_an_unsupported_kind_is_recorded_as_refused(self):
        envelope = bridge.enqueue(self.peer, "swap.propose", {"x": 1}).envelope()
        response = self._post(envelope)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "unsupported")
        self.assertEqual(ClearingInbox.objects.get().state, ClearingInbox.REJECTED)


# ── Trustlines and holds ─────────────────────────────────────────────────


class _Funded(TestCase):
    unit = "FND"

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id=f"p-{self.unit}",
                                      client_id=f"c-{self.unit}")
        self.reserve = LedgerAccount.objects.create(
            code=f"RES-{self.unit}", name="Reserve",
            account_type=AccountType.RESERVE)
        self.asset = create_currency(
            name=f"{self.unit} token", unit_name=self.unit,
            total_supply=Decimal("1000"), decimals=2,
            reserve_account=self.reserve, reference=f"mk-{self.unit}")
        self.alice = LedgerAccount.objects.create(
            code=f"ALICE-{self.unit}", name="Alice", account_type=AccountType.USER)
        self.bob = LedgerAccount.objects.create(
            code=f"BOB-{self.unit}", name="Bob", account_type=AccountType.USER)
        distribute_asset(asset=self.asset, recipient_account=self.alice,
                         amount=Decimal("100"), reference=f"fund-{self.unit}")
        self.line = trustline_service.open_trustline(
            asset=self.asset, peer=self.peer, issued_here=True,
            max_owed=Decimal("500"))

    def _base(self, amount):
        return to_base_units(Decimal(amount), self.asset.decimals)

    def _hold(self, amount="10", **kw):
        return holds_service.create_hold(
            shared=self.line, origin_account=self.alice,
            amount_base=self._base(amount),
            purpose=ClearingHold.PURPOSE_TRANSFER, **kw)


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class TrustlineEdgeTests(_Funded):
    unit = "TLE"

    def test_the_settlement_threshold_defaults_to_half_the_limit(self):
        self.assertEqual(self.line.max_owed_base_units, self._base("500"))
        self.assertEqual(self.line.settlement_threshold_base_units, self._base("250"))

    def test_an_explicit_settlement_threshold_is_kept(self):
        other = make_peer(platform_id="thr", client_id="thr",
                          status=LedgerPeer.STATUS_ACTIVE)
        line = trustline_service.open_trustline(
            asset=self.asset, peer=other, issued_here=True,
            max_owed=Decimal("100"), settlement_threshold=Decimal("7"))
        self.assertEqual(line.settlement_threshold_base_units, self._base("7"))

    def test_the_mirror_side_has_escrow_but_no_vostro(self):
        shared = trustline_service.ensure_mirror_asset(
            unit_name="MRA", decimals=2, peer=self.peer, max_owed=Decimal("10"))
        self.assertIsNotNone(shared.escrow_account)
        self.assertIsNone(shared.vostro_account)
        self.assertEqual(shared.asset.reserve_account.account_type,
                         AccountType.RESERVE)

    def test_ensuring_a_mirror_twice_returns_the_same_trustline(self):
        first = trustline_service.ensure_mirror_asset(
            unit_name="MRB", decimals=2, peer=self.peer, max_owed=Decimal("10"))
        again = trustline_service.ensure_mirror_asset(
            unit_name="MRB", decimals=2, peer=self.peer, max_owed=Decimal("99"))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(again.max_owed_base_units, self._base("10"))

    def test_the_home_position_is_what_the_peer_holds_of_ours(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("30"))
        # Held, not yet posted: the position has not moved.
        self.assertEqual(trustline_service.net_position_base(self.line), 0)
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(record.uuid)})
        self.assertEqual(trustline_service.net_position_base(self.line),
                         self._base("30"))

    def test_a_disabled_trustline_refuses_any_movement(self):
        SharedAsset.objects.filter(pk=self.line.pk).update(enabled=False)
        self.line.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "disabled"):
            trustline_service.check_credit(self.line, 1)

    def test_a_peer_that_is_not_active_refuses_any_movement(self):
        LedgerPeer.objects.filter(pk=self.peer.pk).update(
            status=LedgerPeer.STATUS_SUSPENDED)
        self.line.refresh_from_db()
        self.line.peer.refresh_from_db()
        with self.assertRaises(ValidationError):
            trustline_service.check_credit(self.line, 1)

    def test_pending_escrow_counts_only_pending_holds(self):
        posted = self._hold("10")
        voided = self._hold("5")
        self._hold("3")
        holds_service.post_hold(posted, destination=self.line.vostro_account)
        holds_service.void_hold(voided)
        self.assertEqual(trustline_service.pending_escrow_base(self.line),
                         self._base("3"))
        self.assertEqual(get_asset_balance(self.asset, self.line.escrow_account),
                         self._base("3"))

    def test_str_says_which_side_is_home(self):
        self.assertEqual(str(self.line), "TLE ↔ p-TLE (home)")


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class HoldEdgeTests(_Funded):
    unit = "HDE"

    def test_a_zero_or_negative_hold_is_refused(self):
        for amount in (0, -5):
            with self.assertRaises(ValidationError):
                holds_service.create_hold(
                    shared=self.line, origin_account=self.alice,
                    amount_base=amount, purpose=ClearingHold.PURPOSE_TRANSFER)
        self.assertFalse(ClearingHold.objects.exists())

    def test_a_hold_larger_than_the_balance_moves_nothing(self):
        with self.assertRaises(ValidationError):
            self._hold("150")
        self.assertFalse(ClearingHold.objects.exists())
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_a_voided_hold_cannot_then_be_posted(self):
        hold = self._hold("10")
        holds_service.void_hold(hold)
        with self.assertRaisesMessage(ValidationError, "cannot post"):
            holds_service.post_hold(hold, destination=self.line.vostro_account)
        self.assertEqual(get_asset_balance(self.asset, self.line.vostro_account), 0)

    def test_voiding_an_expired_hold_again_is_a_quiet_no_op(self):
        hold = self._hold("10")
        holds_service.void_hold(hold, expired=True)
        again = holds_service.void_hold(hold)
        self.assertEqual(again.state, ClearingHold.EXPIRED)
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("100"))

    def test_the_sweeper_leaves_fresh_and_settled_holds_alone(self):
        fresh = self._hold("1")
        settled = self._hold("2")
        holds_service.post_hold(settled, destination=self.line.vostro_account)
        ClearingHold.objects.filter(pk=settled.pk).update(
            expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(holds_service.expire_stale_holds(), 0)
        fresh.refresh_from_db()
        settled.refresh_from_db()
        self.assertEqual(fresh.state, ClearingHold.PENDING)
        self.assertEqual(settled.state, ClearingHold.POSTED)

    @override_settings(CLEARING_HOLD_TTL_SECONDS=60)
    def test_the_hold_lifetime_follows_the_setting(self):
        before = timezone.now()
        hold = self._hold("1", extra_ttl_seconds=30)
        self.assertGreaterEqual(hold.expires_at, before + timedelta(seconds=90))
        self.assertLess(hold.expires_at, before + timedelta(seconds=120))

    @override_settings(CLEARING_HOLD_TTL_SECONDS=60,
                       CLEARING_HOLD_SENDER_MARGIN_SECONDS=600)
    def test_the_sender_margin_is_what_separates_hold_and_deadline(self):
        record = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("1"))
        gap = record.hold.expires_at - record.deadline
        self.assertGreaterEqual(gap, timedelta(seconds=599))
        self.assertLess(gap, timedelta(seconds=660))

    def test_str_names_purpose_and_state(self):
        hold = self._hold("1")
        self.assertEqual(str(hold), f"transfer hold {hold.uuid} (pending)")


# ── The transfer protocol ────────────────────────────────────────────────


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class TransferRefusalTests(_Funded):
    unit = "TRF"

    def _prepare(self, **over):
        payload = {
            "transfer_uuid": str(uuid_lib.uuid4()), "unit_name": "TRF",
            "amount_base_units": self._base("1"),
            "receiver_account_code": self.bob.code,
            "sender_account_code": "REMOTE-ALICE",
        }
        payload.update(over)
        return payload

    def _refused_with(self, payload):
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            transfers.apply_prepare(self.peer, payload)
        return ctx.exception.status

    def test_sending_on_another_peers_trustline_is_refused(self):
        stranger = make_peer(platform_id="stranger", client_id="stranger",
                             status=LedgerPeer.STATUS_ACTIVE)
        with self.assertRaisesMessage(ValidationError, "not shared with this peer"):
            transfers.send_to_peer(
                peer=stranger, shared=self.line, origin_account=self.alice,
                remote_account_code="R", amount_base=self._base("1"))
        self.assertFalse(ClearingOutbox.objects.exists())

    def test_an_unknown_receiving_account_is_refused_with_a_signed_reject(self):
        payload = self._prepare(receiver_account_code="GHOST")
        self.assertEqual(self._refused_with(payload), "unknown-account")
        reject = ClearingOutbox.objects.get(kind="transfer.reject")
        self.assertEqual(reject.payload["transfer_uuid"], payload["transfer_uuid"])
        self.assertEqual(reject.payload["status"], "unknown-account")

    def test_a_non_positive_amount_is_refused(self):
        self.assertEqual(self._refused_with(self._prepare(amount_base_units=0)),
                         "bad-amount")
        self.assertEqual(get_asset_balance(self.asset, self.bob), 0)

    def test_a_disabled_trustline_does_not_carry_value(self):
        SharedAsset.objects.filter(pk=self.line.pk).update(enabled=False)
        self.assertEqual(self._refused_with(self._prepare()), "no-trustline")

    def test_a_fulfill_or_reject_for_an_unknown_transfer_is_refused(self):
        for handler in (transfers.apply_fulfill, transfers.apply_reject):
            with self.assertRaises(bridge.InboxRefusal) as ctx:
                handler(self.peer, {"transfer_uuid": str(uuid_lib.uuid4())})
            self.assertEqual(ctx.exception.status, "unknown-transfer")

    def test_a_fulfill_cannot_name_an_inbound_transfer(self):
        # Fund the position, then receive: the INBOUND row must not be
        # mistaken for one of ours to settle.
        out = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("20"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(out.uuid)})
        inbound = self._prepare()
        transfers.apply_prepare(self.peer, inbound)
        with self.assertRaises(bridge.InboxRefusal):
            transfers.apply_fulfill(self.peer,
                                    {"transfer_uuid": inbound["transfer_uuid"]})

    def test_a_reject_after_the_fulfill_cannot_refund_settled_value(self):
        out = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("10"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(out.uuid)})
        with self.assertRaises(ValidationError):
            transfers.apply_reject(self.peer, {"transfer_uuid": str(out.uuid),
                                               "reason": "late"})
        self.assertEqual(get_asset_balance(self.asset, self.alice), self._base("90"))
        out.refresh_from_db()
        self.assertEqual(out.state, ClearingTransfer.FULFILLED)

    def test_the_reject_reason_is_kept_and_trimmed(self):
        out = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("1"))
        transfers.apply_reject(self.peer, {"transfer_uuid": str(out.uuid),
                                           "reason": "x" * 500})
        out.refresh_from_db()
        self.assertEqual(out.reason, "x" * 200)

    def test_an_unsupported_kind_is_refused_by_the_dispatcher(self):
        with self.assertRaises(bridge.InboxRefusal) as ctx:
            transfers.dispatch(self.peer, "swap.execute", {})
        self.assertEqual(ctx.exception.status, "unsupported")

    def test_a_replayed_prepare_through_the_bridge_credits_once(self):
        envelope = bridge.enqueue(self.peer, "transfer.prepare", self._prepare(
            amount_base_units=self._base("5"))).envelope()
        # The home side needs a position to pay out of.
        out = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.alice,
            remote_account_code="R", amount_base=self._base("50"))
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(out.uuid)})

        first = bridge.receive(self.peer, envelope, transfers.dispatch)
        again = bridge.receive(self.peer, envelope, transfers.dispatch)
        self.assertEqual(first, again)
        self.assertEqual(get_asset_balance(self.asset, self.bob), self._base("5"))


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class MirrorSideTests(TestCase):
    """The accounting direction on the side that does NOT issue the asset:
    credits are issued from the mirror reserve, and value sent home is burned
    back into it."""

    def setUp(self):
        self.peer = _self_pinned_peer(platform_id="home", client_id="home")
        self.line = trustline_service.ensure_mirror_asset(
            unit_name="MIR", decimals=2, peer=self.peer, max_owed=Decimal("100"))
        self.asset = self.line.asset
        self.bob = LedgerAccount.objects.create(
            code="BOB-MIR", name="Bob", account_type=AccountType.USER)

    def _receive(self, amount):
        return transfers.apply_prepare(self.peer, {
            "transfer_uuid": str(uuid_lib.uuid4()), "unit_name": "MIR",
            "amount_base_units": to_base_units(Decimal(amount), 2),
            "receiver_account_code": "BOB-MIR", "sender_account_code": "HOME-A",
        })

    def test_a_credit_issues_mirrored_units_out_of_the_reserve(self):
        self._receive("10")
        self.assertEqual(get_asset_balance(self.asset, self.bob), 1000)
        self.assertEqual(trustline_service.net_position_base(self.line), 1000)

    def test_sending_home_burns_back_into_the_reserve_on_fulfill(self):
        self._receive("10")
        out = transfers.send_to_peer(
            peer=self.peer, shared=self.line, origin_account=self.bob,
            remote_account_code="HOME-A", amount_base=400)
        transfers.apply_fulfill(self.peer, {"transfer_uuid": str(out.uuid)})
        self.assertEqual(get_asset_balance(self.asset, self.bob), 600)
        self.assertEqual(trustline_service.net_position_base(self.line), 600)
        report = verify_asset_ledger(self.asset)
        self.assertTrue(report["entries_balanced"])
