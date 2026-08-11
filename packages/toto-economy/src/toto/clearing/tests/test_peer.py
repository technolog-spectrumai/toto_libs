"""Stage-1 suite: peer credentials, the wire format, and the handshake."""

from __future__ import annotations

import base64
import json

from django.test import override_settings
from toto.assets.testing import LedgerTestCase as TestCase

from toto.clearing.models import LedgerPeer
from toto.clearing.services import handshake as hs
from toto.clearing.services import wire

FERNET_KEY = "zqx3Wt0nqTfKqBPXCsFtHQOMoO0v8kBn8ZQmFqBLLwo="


def make_peer(role=LedgerPeer.ROLE_CHILD, **kw):
    defaults = dict(
        name="other", base_url="https://peer.test", role=role,
        platform_id=f"peer-{role}", client_id="link-1",
    )
    defaults.update(kw)
    peer = LedgerPeer.objects.create(**defaults)
    peer.set_secret("s3cret")
    peer.save()
    return peer


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class PeerCredentialTests(TestCase):
    def test_secret_roundtrip_and_rotation_grace(self):
        peer = make_peer()
        self.assertTrue(peer.check_secret("s3cret"))
        self.assertFalse(peer.check_secret("wrong"))
        peer.set_secret("n3w")
        peer.save()
        # current AND previous accepted — the rotation grace window.
        self.assertTrue(peer.check_secret("n3w"))
        self.assertTrue(peer.check_secret("s3cret"))
        self.assertFalse(peer.check_secret("older-still"))

    def test_secrets_never_stored_readable(self):
        peer = make_peer()
        raw = bytes(peer.client_secret_encrypted)
        self.assertNotIn(b"s3cret", raw)


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class WireTests(TestCase):
    def test_sign_verify_roundtrip(self):
        us = make_peer(platform_id="us", client_id="a")
        # Simulate the peer verifying: pin OUR public key on a second row.
        us.ensure_keypair(); us.save()
        them = make_peer(platform_id="them", client_id="b",
                         peer_public_key_pem=us.our_public_key_pem)
        envelope = wire.sign_envelope(us, "transfer.prepare", {"amount": "5"})
        self.assertTrue(wire.verify_envelope(them, envelope))

    def test_kind_is_bound_into_the_signature(self):
        # A prepare signature replayed as a fulfill must fail: the domain
        # context is inside the signed bytes.
        us = make_peer(platform_id="us2", client_id="c")
        us.ensure_keypair(); us.save()
        them = make_peer(platform_id="them2", client_id="d",
                         peer_public_key_pem=us.our_public_key_pem)
        envelope = wire.sign_envelope(us, "transfer.prepare", {"amount": "5"})
        forged = dict(envelope, kind="transfer.fulfill")
        self.assertFalse(wire.verify_envelope(them, forged))

    def test_payload_tamper_detected(self):
        us = make_peer(platform_id="us3", client_id="e")
        us.ensure_keypair(); us.save()
        them = make_peer(platform_id="them3", client_id="f",
                         peer_public_key_pem=us.our_public_key_pem)
        envelope = wire.sign_envelope(us, "checkpoint", {"root": "abc"})
        envelope["payload"]["root"] = "abd"
        self.assertFalse(wire.verify_envelope(them, envelope))

    def test_unknown_kind_refused(self):
        us = make_peer(platform_id="us4", client_id="g")
        self.assertFalse(wire.verify_envelope(us, {"kind": "nope", "payload": {}}))


@override_settings(FIELD_ENCRYPTION_KEY=FERNET_KEY)
class HandshakeTests(TestCase):
    def _basic(self, client_id="link-1", secret="s3cret"):
        token = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        return {"HTTP_AUTHORIZATION": f"Basic {token}"}

    def test_first_contact_pins_and_activates(self):
        # "them" is the caller's own row for us; we hold "peer" for them.
        caller = make_peer(platform_id="caller", client_id="x")
        hello = hs.build_hello(caller)
        peer = make_peer(platform_id="callee-view", client_id="link-1")
        result = hs.apply_hello(peer, hello)
        peer.refresh_from_db()
        self.assertEqual(result["status"], "pinned")
        self.assertEqual(peer.status, LedgerPeer.STATUS_ACTIVE)
        self.assertEqual(peer.peer_public_key_pem, caller.our_public_key_pem)

    def test_epoch_change_suspends_never_repins(self):
        caller = make_peer(platform_id="caller2", client_id="y")
        hello = hs.build_hello(caller)
        peer = make_peer(platform_id="callee2", client_id="link-1b")
        hs.apply_hello(peer, hello)
        # The peer resets: new epoch, same key. Must suspend.
        caller.epoch = "00000000-0000-0000-0000-000000000000"
        caller.save()
        hello2 = hs.build_hello(caller)
        peer.refresh_from_db()
        result = hs.apply_hello(peer, hello2)
        peer.refresh_from_db()
        self.assertEqual(result["status"], "suspended-epoch-or-key-change")
        self.assertEqual(peer.status, LedgerPeer.STATUS_SUSPENDED)

    def test_endpoint_requires_auth_and_answers_hello(self):
        peer = make_peer(platform_id="ep", client_id="link-1")
        caller = make_peer(platform_id="ep-caller", client_id="z")
        hello = hs.build_hello(caller)

        resp = self.client.post("/clearing/api/handshake/",
                                data=json.dumps(hello),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)

        resp = self.client.post("/clearing/api/handshake/",
                                data=json.dumps(hello),
                                content_type="application/json",
                                **self._basic())
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["status"], "pinned")
        self.assertIn("hello", body)

    def test_suspended_peer_fails_auth_shaped(self):
        peer = make_peer(platform_id="susp", client_id="link-1",
                         status=LedgerPeer.STATUS_SUSPENDED)
        resp = self.client.post("/clearing/api/handshake/", data="{}",
                                content_type="application/json",
                                **self._basic())
        self.assertEqual(resp.status_code, 401)
