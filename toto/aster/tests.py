"""Tests for the aster Tor-signalling directory.

Run (faros settings) from the repo root:
    DJANGO_SETTINGS_MODULE=faros.settings python faros/manage.py test toto.aster
"""
import json

from django.contrib.auth import get_user_model
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AsterAddress, AsterDevice

NODE_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NODE_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
RELAY = "https://relay.iroh.example/"


def _json(resp):
    return json.loads(resp.content or b"{}")


class AsterTestBase(TestCase):
    def setUp(self):
        User = get_user_model()
        self.alice = User.objects.create_user(username="alice", password="pw-alice")
        self.bob = User.objects.create_user(username="bob", password="pw-bob")
        self.client = Client()

    # helpers ----------------------------------------------------------------
    def as_user(self, user):
        c = Client()
        c.force_login(user)
        return c

    def register(self, client, node_id, label=""):
        return client.post(
            reverse("aster:device"),
            data=json.dumps({"node_id": node_id, "label": label}),
            content_type="application/json",
        )

    def publish(self, client, node_id, relay_url=RELAY):
        return client.post(
            reverse("aster:addr_publish"),
            data=json.dumps({"node_id": node_id, "relay_url": relay_url}),
            content_type="application/json",
        )

    def resolve_addr(self, client, node_id):
        return client.get(reverse("aster:addr_resolve", args=[node_id]))

    def stale(self, node_id, age_seconds=10_000):
        """Force an address's updated_at into the past, bypassing auto_now."""
        old = timezone.now() - timezone.timedelta(seconds=age_seconds)
        AsterAddress.objects.filter(device__node_id=node_id).update(updated_at=old)


class AuthTests(AsterTestBase):
    def test_all_endpoints_require_auth(self):
        anon = Client()
        self.assertEqual(self.register(anon, NODE_A).status_code, 401)
        self.assertEqual(self.publish(anon, NODE_A).status_code, 401)
        self.assertEqual(self.resolve_addr(anon, NODE_A).status_code, 401)
        self.assertEqual(anon.get(reverse("aster:resolve") + "?user=alice").status_code, 401)

    def test_bearer_session_token_authenticates(self):
        # Mirror telegraph's auth: Authorization: Bearer <session_key>.
        s = SessionStore()
        s["_auth_user_id"] = str(self.alice.pk)
        s.save()
        anon = Client()  # no session cookie — only the bearer header
        resp = anon.post(
            reverse("aster:device"),
            data=json.dumps({"node_id": NODE_A}),
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {s.session_key}",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(AsterDevice.objects.filter(node_id=NODE_A, user=self.alice).exists())

    def test_options_preflight_ok_with_cors(self):
        resp = self.client.options(reverse("aster:device"), HTTP_ORIGIN="tauri://localhost")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Access-Control-Allow-Origin"], "tauri://localhost")
        # header-name casing varies by which CORS layer answers the preflight
        self.assertIn("authorization", resp["Access-Control-Allow-Headers"].lower())


class DeviceTests(AsterTestBase):
    def test_register_creates_device(self):
        c = self.as_user(self.alice)
        resp = self.register(c, NODE_A, label="laptop")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_json(resp)["node_id"], NODE_A)
        dev = AsterDevice.objects.get(node_id=NODE_A)
        self.assertEqual(dev.user, self.alice)
        self.assertEqual(dev.label, "laptop")

    def test_register_is_idempotent_refresh(self):
        c = self.as_user(self.alice)
        self.register(c, NODE_A, label="laptop")
        self.register(c, NODE_A, label="laptop-renamed")
        self.assertEqual(AsterDevice.objects.filter(node_id=NODE_A).count(), 1)
        self.assertEqual(AsterDevice.objects.get(node_id=NODE_A).label, "laptop-renamed")

    def test_cross_user_registration_rejected(self):
        self.register(self.as_user(self.alice), NODE_A)
        resp = self.register(self.as_user(self.bob), NODE_A)
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(AsterDevice.objects.get(node_id=NODE_A).user, self.alice)

    def test_missing_node_id_is_400(self):
        resp = self.as_user(self.alice).post(
            reverse("aster:device"), data=json.dumps({}), content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_json_is_400(self):
        resp = self.as_user(self.alice).post(
            reverse("aster:device"), data="{not json", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)


class AddressPublishResolveTests(AsterTestBase):
    def setUp(self):
        super().setUp()
        self.alice_c = self.as_user(self.alice)
        self.register(self.alice_c, NODE_A)

    def test_publish_then_resolve_returns_relay(self):
        self.assertEqual(self.publish(self.alice_c, NODE_A).status_code, 200)
        resp = self.resolve_addr(self.as_user(self.bob), NODE_A)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_json(resp)["relay_url"], RELAY)

    def test_publish_requires_owning_the_device(self):
        # bob can't publish for alice's node_id (he doesn't own that device).
        resp = self.publish(self.as_user(self.bob), NODE_A)
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(AsterAddress.objects.filter(device__node_id=NODE_A).exists())

    def test_publish_unregistered_device_is_403(self):
        resp = self.publish(self.alice_c, NODE_B)  # never registered
        self.assertEqual(resp.status_code, 403)

    def test_publish_missing_relay_is_400(self):
        resp = self.alice_c.post(
            reverse("aster:addr_publish"),
            data=json.dumps({"node_id": NODE_A}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_resolve_unknown_node_is_404(self):
        resp = self.resolve_addr(self.alice_c, NODE_B)
        self.assertEqual(resp.status_code, 404)

    def test_resolve_stale_address_is_404(self):
        self.publish(self.alice_c, NODE_A)
        self.stale(NODE_A)  # older than the TTL
        resp = self.resolve_addr(self.alice_c, NODE_A)
        self.assertEqual(resp.status_code, 404)

    def test_republish_refreshes_freshness(self):
        self.publish(self.alice_c, NODE_A)
        self.stale(NODE_A)
        self.assertEqual(self.resolve_addr(self.alice_c, NODE_A).status_code, 404)
        # republishing with a new relay URL makes it fresh again
        self.publish(self.alice_c, NODE_A, relay_url="https://relay2.example/")
        resp = self.resolve_addr(self.alice_c, NODE_A)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_json(resp)["relay_url"], "https://relay2.example/")
        self.assertEqual(AsterAddress.objects.filter(device__node_id=NODE_A).count(), 1)


class ResolveByUserTests(AsterTestBase):
    def test_lists_user_node_ids(self):
        c = self.as_user(self.alice)
        self.register(c, NODE_A)
        self.register(c, NODE_B)
        resp = self.as_user(self.bob).get(reverse("aster:resolve") + "?user=alice")
        self.assertEqual(resp.status_code, 200)
        self.assertCountEqual(_json(resp)["node_ids"], [NODE_A, NODE_B])

    def test_unknown_user_returns_empty(self):
        resp = self.as_user(self.alice).get(reverse("aster:resolve") + "?user=nobody")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(_json(resp)["node_ids"], [])

    def test_missing_user_param_is_400(self):
        resp = self.as_user(self.alice).get(reverse("aster:resolve"))
        self.assertEqual(resp.status_code, 400)

    def test_kind_filter_selects_the_right_endpoint(self):
        c = self.as_user(self.alice)
        c.post(
            reverse("aster:device"),
            data=json.dumps({"node_id": NODE_A, "kind": "gossip"}),
            content_type="application/json",
        )
        c.post(
            reverse("aster:device"),
            data=json.dumps({"node_id": NODE_B, "kind": "vox"}),
            content_type="application/json",
        )
        resp = self.as_user(self.bob).get(reverse("aster:resolve") + "?user=alice&kind=vox")
        self.assertEqual(_json(resp)["node_ids"], [NODE_B])
        resp = self.as_user(self.bob).get(reverse("aster:resolve") + "?user=alice&kind=gossip")
        self.assertEqual(_json(resp)["node_ids"], [NODE_A])


class ModelTests(AsterTestBase):
    def test_address_is_fresh_boundary(self):
        dev = AsterDevice.objects.create(user=self.alice, node_id=NODE_A)
        addr = AsterAddress.objects.create(device=dev, relay_url=RELAY)
        self.assertTrue(addr.is_fresh())
        AsterAddress.objects.filter(pk=addr.pk).update(
            updated_at=timezone.now() - timezone.timedelta(seconds=10_000)
        )
        addr.refresh_from_db()
        self.assertFalse(addr.is_fresh())
