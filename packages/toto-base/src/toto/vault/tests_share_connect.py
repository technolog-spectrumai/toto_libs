"""Storage → Management: a bucket shared with another Zenobia, both sides
(``share_views``, 2026-09-30).

The sharing side makes a share (a ``BucketGrant``) and shows its pairing code
and QR code ONCE; lists, rotates and revokes shares. The connecting side
reads a code (pasted, scanned or from an image — in the browser), previews
what it grants, tests the connection and connects (a ``BucketPeer`` and a
bucket, in one transaction). A superuser on the Superuser plan only.

"Another Zenobia" is the loopback of ``tests_mirror``: one database, the
real peer views behind the client — so a code minted by the share door is
connected by the connect door of the same test.

The code, its api key, its magic token and its grant id must never come
back: not in JSON, the page after, the session, a log or the audit chain.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_share_connect
"""

import base64
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import timedelta
from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from . import manage_views, share_views
from .models import Bucket, StorageBackend
from .peer_client import PeerClient
from .peering import BucketGrant, BucketPeer, pairing_code_for
from .tests_management import ManageFixture
from .tests_mirror import DownHttp, LoopbackHttp
from .tests_storage_adapters import PERSISTENT, audit_actions, audit_dump, private_dns, public_dns

User = get_user_model()

PEER_HOST = "https://peer.example.org"


class LeakyHttp:
    """A transport failure that quotes the URL it called — the URL carries
    the grant's id and magic token."""

    def request(self, method, url, **kwargs):
        raise ConnectionError(f"Max retries exceeded with url: {url}")


@contextmanager
def captured_logs():
    """Every log record, every logger, every level, as text."""
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            try:
                records.append(record.getMessage())
            except Exception:  # noqa: BLE001
                records.append(str(record.msg))

    handler = Keep(level=logging.DEBUG)
    root = logging.getLogger()
    before = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(before)


def secrets_of(code: str) -> list:
    payload = json.loads(base64.b64decode(code))
    return [code, payload["api_key"], payload["magic_token"], payload["grant_uid"]]


class ShareFixture(ManageFixture):
    def mint(self, bucket=None, **data):
        post = {"label": "Placidia", "may_list": "1", "expires": "7", "address": PEER_HOST}
        post.update(data)
        return self.as_root().post(reverse("vault:manage_share", args=[(bucket or self.local).pk]), post)

    def rotate(self, grant, **data):
        post = {"expires": "keep", "address": PEER_HOST}
        post.update(data)
        return self.as_root().post(reverse("vault:manage_share_rotate", args=[grant.pk]), post)

    @staticmethod
    def code_of(response) -> str:
        found = re.search(r'data-testid="share-code"[^>]*>([^<]*)</textarea>', response.content.decode())
        return html.unescape(found.group(1)).strip() if found else ""

    @staticmethod
    def payload(code) -> dict:
        return json.loads(base64.b64decode(code))

    def exported(self, *, expires_at="default", host=PEER_HOST, bucket=None, **rights):
        """A code minted on "the other Zenobia" (this database, the loopback)."""
        grant = BucketGrant.objects.create(label="loop", bucket=bucket or self.local,
                                           **(rights or {"may_list": True, "may_download": True}))
        if expires_at != "default":
            grant.expires_at = expires_at
        raw = grant.issue_api_key()
        grant.save()
        return pairing_code_for(grant, raw, host=host), grant, raw

    def assert_no_secret(self, text, secrets):
        text = text if isinstance(text, str) else json.dumps(text, default=str)
        for value in secrets:
            self.assertNotIn(str(value), text)

    def session_dump(self) -> str:
        return json.dumps(dict(self.client.session.items()), default=str)


# ---------------------------------------------------------------------------
# Who may use the doors
# ---------------------------------------------------------------------------

class ShareGateTests(ShareFixture):
    def setUp(self):
        self.code, self.grant, self.raw = self.exported()
        self.hash = self.grant.api_key_hash

    def doors(self):
        g = self.grant.pk
        return [
            ("get", reverse("vault:manage_shares", args=[self.local.pk]), {}),
            ("post", reverse("vault:manage_share", args=[self.local.pk]),
             {"label": "Sneaky", "may_list": "1", "expires": "7"}),
            ("post", reverse("vault:manage_share_rotate", args=[g]), {"expires": "keep"}),
            ("post", reverse("vault:manage_share_revoke", args=[g]), {}),
            ("post", reverse("vault:manage_connect_preview"), {"pairing_code": self.code}),
            ("post", reverse("vault:manage_connect_test"), {"pairing_code": self.code}),
            ("post", reverse("vault:manage_connect"), {"pairing_code": self.code, "name": "Sneaky",
                                                       "owner": self.member.pk}),
            ("post", reverse("vault:manage_connect_renew"), {"pairing_code": self.code}),
        ]

    def assert_nothing_changed(self):
        self.grant.refresh_from_db()
        self.assertTrue(self.grant.is_active)
        self.assertEqual(self.grant.api_key_hash, self.hash)
        self.assertEqual(BucketGrant.objects.count(), 1)
        self.assertEqual(BucketPeer.objects.count(), 1)          # the fixture's own
        self.assertFalse(Bucket.objects.filter(name="Sneaky").exists())

    def test_anonymous_visitors_are_refused(self):
        with mock.patch("toto.vault.peer_client._http", return_value=LoopbackHttp()):
            for method, url, data in self.doors():
                with self.subTest(url=url):
                    response = getattr(self.client, method)(url, data)
                    if response.status_code == 403:
                        self.assertEqual(response["Content-Type"], "application/json")
                        self.assertIn("error", response.json())
                    else:   # a host whose site-wide login gate answers first
                        self.assertEqual(response.status_code, 302)
                        self.assertIn("login", response["Location"])
                    self.assert_no_secret(response.content.decode(), secrets_of(self.code))
        self.assert_nothing_changed()

    def test_members_staff_and_a_superuser_without_the_plan_are_refused(self):
        from django.apps import apps

        visitors = [("member", self.member), ("staff", self.staff)]
        if apps.is_installed("toto.subscriptions"):
            visitors.append(("superuser without the plan", self.bare))
        with mock.patch("toto.vault.peer_client._http", return_value=LoopbackHttp()):
            for label, user in visitors:
                self.client.force_login(user)
                for method, url, data in self.doors():
                    with self.subTest(visitor=label, url=url):
                        response = getattr(self.client, method)(url, data)
                        self.assertEqual(response.status_code, 403)
                        self.assertEqual(response["Content-Type"], "application/json")
                        self.assertIn("Superuser plan", response.json()["error"])
        self.assert_nothing_changed()

    def test_the_refusal_comes_before_the_lookup(self):
        self.client.force_login(self.member)
        for url in (reverse("vault:manage_shares", args=[999999]),
                    reverse("vault:manage_share", args=[999999]),
                    reverse("vault:manage_share_rotate", args=[999999]),
                    reverse("vault:manage_share_revoke", args=[999999])):
            with self.subTest(url=url):
                method = self.client.get if url.endswith("/shares/") else self.client.post
                self.assertEqual(method(url).status_code, 403)

    def test_the_plan_holder_gets_404_for_what_does_not_exist(self):
        client = self.as_root()
        self.assertEqual(client.get(reverse("vault:manage_shares", args=[999999])).status_code, 404)
        self.assertEqual(client.post(reverse("vault:manage_share_rotate", args=[999999])).status_code, 404)
        self.assertEqual(client.post(reverse("vault:manage_share_revoke", args=[999999])).status_code, 404)

    def test_the_doors_that_change_things_refuse_get(self):
        client = self.as_root()
        for url in (reverse("vault:manage_share", args=[self.local.pk]),
                    reverse("vault:manage_share_rotate", args=[self.grant.pk]),
                    reverse("vault:manage_share_revoke", args=[self.grant.pk]),
                    reverse("vault:manage_connect_preview"), reverse("vault:manage_connect_test"),
                    reverse("vault:manage_connect"), reverse("vault:manage_connect_renew")):
            with self.subTest(url=url):
                self.assertEqual(client.get(url).status_code, 405)
        self.assertEqual(client.post(reverse("vault:manage_shares", args=[self.local.pk])).status_code, 405)

    def test_a_post_without_the_csrf_token_is_refused(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.root)
        response = client.post(reverse("vault:manage_share", args=[self.local.pk]),
                               {"label": "Sneaky", "may_list": "1"})
        self.assertEqual(response.status_code, 403)
        response = client.post(reverse("vault:manage_connect_preview"), {"pairing_code": self.code})
        self.assertEqual(response.status_code, 403)
        self.assert_nothing_changed()


# ---------------------------------------------------------------------------
# The sharing side
# ---------------------------------------------------------------------------

class SharePageTests(ShareFixture):
    def test_share_is_offered_on_this_servers_and_s3_buckets_never_on_a_mount(self):
        body = self.page()
        self.assertIn(f'data-testid="bucket-share-{self.local.pk}"', body)
        self.assertIn(f'data-testid="bucket-share-{self.cloud.pk}"', body)
        self.assertNotIn(f'data-testid="bucket-share-{self.mount.pk}"', body)
        self.assertIn(reverse("vault:manage_shares", args=[self.local.pk]), body)

    def test_the_form_starts_with_every_right_off_and_seven_days(self):
        body = self.page()
        for right in ("may_list", "may_download", "may_upload", "may_delete"):
            tag = re.search(rf'<input type="checkbox" name="{right}"[^>]*>', body).group(0)
            with self.subTest(right=right):
                self.assertNotIn("checked", tag)
        self.assertRegex(body, r'<option value="7" selected>')
        self.assertIn('data-testid="bucket-share-delete-warning"', body)
        self.assertIn("destroy files in this bucket for good", body)
        # The browser's own starting state: nothing ticked, seven days.
        self.assertIn("may_list: false, may_download: false", body)
        self.assertIn("may_upload: false, may_delete: false", body)

    def test_the_qr_code_is_drawn_locally_never_by_a_service(self):
        body = self.page()
        self.assertIn("vendor/qrcodejs/qrcode.min.js", body)
        self.assertIn("vendor/jsqr/jsQR.js", body)
        for service in ("googleapis", "qrserver", "quickchart", "goqr", "qr-code-generator"):
            self.assertNotIn(service, body)

    def test_federated_hosts_are_suggested(self):
        with mock.patch("toto.vault.share_views.federated_host_choices",
                        return_value=[("https://placidia.example.org", "Placidia (https://placidia.example.org)")]):
            body = self.page()
        self.assertIn('<option value="Placidia (https://placidia.example.org)">', body)     # who it is for
        self.assertIn('<option value="https://placidia.example.org">', body)                # the address


class OneSwitchTests(ShareFixture):
    """The header's Connect, every row's Share and the modals read ONE switch,
    the page's ``share_connect_config`` (2026-10-01, todo 29.10). The
    ``remote_buckets_enabled`` tag asked the host flag again for each button,
    beside the modals' own switch; it is gone."""

    def test_the_page_works_its_switch_out_once(self):
        with mock.patch("toto.vault.share_views.page_config",
                        wraps=share_views.page_config) as config:
            body = self.page()
        self.assertEqual(config.call_count, 1)
        self.assertIn('data-testid="bucket-connect-open"', body)
        self.assertIn(f'data-testid="bucket-share-{self.local.pk}"', body)
        self.assertIn('data-testid="bucket-share-root"', body)

    def test_the_tag_is_gone_and_no_template_loads_it(self):
        from pathlib import Path

        from django.conf import settings
        from django.template.utils import get_app_template_dirs

        from .templatetags import vault_flags

        self.assertNotIn("remote_buckets_enabled", vault_flags.register.tags)
        self.assertIn("superuser_plan", vault_flags.register.filters)
        roots = list(get_app_template_dirs("templates"))
        for engine in settings.TEMPLATES:
            roots += [Path(d) for d in engine.get("DIRS", [])]
        offenders = [str(path) for root in roots for path in Path(root).rglob("*.html")
                     if "remote_buckets_enabled" in path.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(offenders, [])


class ShareTests(ShareFixture):
    def test_a_share_shows_its_code_and_qr_code_once(self):
        with captured_logs() as logs:
            response = self.mint(may_list="1", may_download="1", expires="30")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertIn("no-store", response["Cache-Control"])
        body = response.content.decode()
        code = self.code_of(response)
        payload = self.payload(code)
        grant = BucketGrant.objects.get(bucket=self.local)
        self.assertEqual(payload["grant_uid"], str(grant.grant_uid))
        self.assertEqual(payload["magic_token"], grant.magic_token)
        self.assertEqual(payload["bucket"], self.local.slug)
        self.assertEqual(payload["rights"], ["may_list", "may_download"])
        self.assertEqual(payload["host"], PEER_HOST)
        self.assertTrue(grant.verify_api_key(payload["api_key"]))
        self.assertNotIn(payload["api_key"], grant.api_key_hash)
        self.assertEqual(grant.label, "Placidia")
        self.assertEqual(grant.created_by, self.root)
        self.assertEqual((grant.may_upload, grant.may_delete), (False, False))
        self.assertAlmostEqual(grant.expires_at, timezone.now() + timedelta(days=30),
                               delta=timedelta(minutes=1))
        # The code, its QR code (drawn in the browser from that very text),
        # Copy, the other side's numbered steps, "not shown again".
        self.assertIn('data-testid="share-qr"', body)
        self.assertIn("data-pairing-qr", body)
        self.assertIn("data-copy-code", body)
        self.assertIn("will not be shown again", body)
        steps = re.search(r'data-testid="share-steps">(.*?)</ol>', body, re.S).group(1)
        self.assertGreaterEqual(steps.count("<li>"), 3)
        self.assertIn("Connect a bucket from another Zenobia", steps)
        self.assertIn("a photo of its QR code", body)
        # Never again: the page after, the list, the session, the logs, the chain.
        secrets = secrets_of(code)
        self.assert_no_secret(self.page(), secrets)
        shares = self.as_root().get(reverse("vault:manage_shares", args=[self.local.pk]))
        self.assert_no_secret(shares.content.decode(), secrets + [grant.api_key_hint])
        self.assert_no_secret(self.session_dump(), secrets)
        self.assert_no_secret("\n".join(logs), secrets)
        self.assertIn("VAULT.BUCKET.SHARED", audit_actions())
        self.assert_no_secret(audit_dump(), secrets)

    def test_this_zenobias_own_address_is_the_default(self):
        code = self.code_of(self.mint(address=""))
        self.assertEqual(self.payload(code)["host"], "http://testserver")

    def test_no_end_date(self):
        code = self.code_of(self.mint(expires="never"))
        self.assertIsNone(BucketGrant.objects.get(bucket=self.local).expires_at)
        self.assertNotIn("expires_at", self.payload(code))

    def test_a_share_needs_who_it_is_for_and_a_right(self):
        response = self.mint(label="  ", may_list="0", expires="12", address="ftp://nowhere")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(set(data["errors"]), {"label", "rights", "expires", "address"})
        self.assertIn("at least one right", data["errors"]["rights"][0])
        self.assertFalse(BucketGrant.objects.exists())
        self.assertNotIn(manage_views.DRAFT_KEY, self.client.session)

    def test_a_mounted_bucket_is_never_shared_onwards(self):
        response = self.mint(bucket=self.mount)
        self.assertEqual(response.status_code, 409)
        self.assertIn("cannot be shared onwards", response.json()["error"])
        self.assertFalse(BucketGrant.objects.exists())
        listing = self.as_root().get(reverse("vault:manage_shares", args=[self.mount.pk])).json()
        self.assertIn("cannot be shared onwards", listing["refusal"])

    def test_a_bucket_being_deleted_is_not_shared(self):
        Bucket.objects.filter(pk=self.local.pk).update(deletion_requested_at=timezone.now())
        response = self.mint()
        self.assertEqual(response.status_code, 409)
        self.assertIn("being deleted", response.json()["error"])
        self.assertFalse(BucketGrant.objects.exists())

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_host_without_external_buckets_neither_shares_nor_connects(self):
        body = self.page()
        self.assertNotIn('data-testid="bucket-share-', body)
        self.assertNotIn('data-testid="bucket-connect-open"', body)
        self.assertNotIn('data-testid="bucket-share-root"', body)
        self.assertNotIn('data-testid="bucket-connect-root"', body)
        response = self.mint()
        self.assertEqual(response.status_code, 409)
        self.assertIn("disabled", response.json()["error"])
        self.assertFalse(BucketGrant.objects.exists())

    def test_the_shared_with_list(self):
        self.mint(label="Placidia", may_list="1", may_delete="1", expires="30")
        self.mint(label="Aurelian", may_download="1", expires="never")
        data = self.as_root().get(reverse("vault:manage_shares", args=[self.local.pk])).json()
        self.assertEqual(data["bucket"], {"pk": self.local.pk, "name": "Alpha"})
        self.assertEqual(data["refusal"], "")
        rows = {row["label"]: row for row in data["shares"]}
        self.assertEqual(set(rows), {"Placidia", "Aurelian"})
        self.assertEqual(rows["Placidia"]["rights_labels"], ["list", "delete"])
        self.assertEqual(rows["Placidia"]["status"], "active")
        self.assertTrue(rows["Placidia"]["expires"])
        self.assertEqual(rows["Aurelian"]["expires"], "")
        self.assertTrue(rows["Aurelian"]["created"])
        self.assertTrue(rows["Aurelian"]["created_by"])
        self.assertEqual(rows["Aurelian"]["last_used"], "")
        for row in data["shares"]:
            self.assertFalse({"grant_uid", "magic_token", "api_key", "api_key_hint", "api_key_hash"} & set(row))
        grant = BucketGrant.objects.get(label="Placidia")
        self.assert_no_secret(json.dumps(data), [str(grant.grant_uid), grant.magic_token, grant.api_key_hint])


class RevokeRotateTests(ShareFixture):
    def manifest(self, grant, key):
        return self.client.get(
            f"/vault/peer/{grant.grant_uid}/{grant.magic_token}/manifest/",
            HTTP_X_VAULT_API_KEY=key)

    def test_revoke_stops_the_share_at_once_and_keeps_the_record(self):
        code = self.code_of(self.mint())
        grant = BucketGrant.objects.get()
        key = self.payload(code)["api_key"]
        self.assertEqual(self.manifest(grant, key).status_code, 200)
        response = self.as_root().post(reverse("vault:manage_share_revoke", args=[grant.pk]))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("revoked", data["message"])
        self.assertEqual(data["shares"][0]["status"], "revoked")
        grant.refresh_from_db()
        self.assertFalse(grant.is_active)
        self.assertEqual(self.manifest(grant, key).status_code, 403)
        self.assertIn("VAULT.BUCKET.SHARE_REVOKED", audit_actions())
        self.assert_no_secret(audit_dump(), secrets_of(code))
        self.assert_no_secret(json.dumps(data), secrets_of(code))
        # Revoked stays revoked: no new key for it.
        again = self.rotate(grant)
        self.assertEqual(again.status_code, 409)
        self.assertIn("revoked", again.json()["error"])
        self.assertEqual(self.code_of(again), "")

    def test_revoking_twice_records_once(self):
        self.mint()
        grant = BucketGrant.objects.get()
        for _ in range(2):
            self.as_root().post(reverse("vault:manage_share_revoke", args=[grant.pk]))
        self.assertEqual(audit_actions().count("VAULT.BUCKET.SHARE_REVOKED"), 1)

    def test_rotate_shows_a_new_code_once_and_the_old_key_stops(self):
        old = self.code_of(self.mint(expires="30"))
        grant = BucketGrant.objects.get()
        expires = grant.expires_at
        old_key = self.payload(old)["api_key"]
        response = self.rotate(grant, expires="keep")
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        body = response.content.decode()
        self.assertIn("New key for", body)
        self.assertIn('data-testid="share-qr"', body)
        self.assertIn("replace the key stored there", body)
        new = self.code_of(response)
        self.assertNotEqual(new, old)
        new_payload = self.payload(new)
        self.assertEqual(new_payload["grant_uid"], str(grant.grant_uid))
        grant.refresh_from_db()
        self.assertFalse(grant.verify_api_key(old_key))
        self.assertTrue(grant.verify_api_key(new_payload["api_key"]))
        self.assertEqual(grant.expires_at, expires)
        self.assertIsNotNone(grant.key_rotated_at)
        self.assertEqual(self.manifest(grant, old_key).status_code, 403)
        self.assertEqual(self.manifest(grant, new_payload["api_key"]).status_code, 200)
        self.assertIn("VAULT.BUCKET.SHARE_ROTATED", audit_actions())
        for code in (old, new):
            self.assert_no_secret(audit_dump(), secrets_of(code))
            self.assert_no_secret(self.page(), secrets_of(code))
            self.assert_no_secret(self.session_dump(), secrets_of(code))

    def test_rotate_can_set_a_new_end_date(self):
        self.mint(expires="1")
        grant = BucketGrant.objects.get()
        self.assertEqual(self.rotate(grant, expires="90").status_code, 200)
        grant.refresh_from_db()
        self.assertAlmostEqual(grant.expires_at, timezone.now() + timedelta(days=90),
                               delta=timedelta(minutes=1))

    def test_an_ended_share_cannot_keep_its_end_date(self):
        self.mint()
        grant = BucketGrant.objects.get()
        BucketGrant.objects.filter(pk=grant.pk).update(expires_at=timezone.now() - timedelta(days=1))
        response = self.rotate(grant, expires="keep")
        self.assertEqual(response.status_code, 400)
        self.assertIn("expires", response.json()["errors"])
        self.assertEqual(self.rotate(grant, expires="7").status_code, 200)
        self.assertFalse(BucketGrant.objects.get().is_expired)


# ---------------------------------------------------------------------------
# The connecting side
# ---------------------------------------------------------------------------

@override_settings(**PERSISTENT)
class ConnectFixture(ShareFixture):
    def setUp(self):
        super().setUp()
        dns = mock.patch("toto.vault.outbound.socket.getaddrinfo", public_dns)
        dns.start()
        self.addCleanup(dns.stop)

    def post(self, name, data, http=None):
        with mock.patch("toto.vault.peer_client._http", return_value=http or LoopbackHttp()):
            return self.as_root().post(reverse(f"vault:{name}"), data)

    def connect(self, code, http=None, **extra):
        data = {"pairing_code": code, "base_url": PEER_HOST, "name": "Mounted Alpha",
                "owner": self.member.pk, "ai_protected": "1"}
        data.update(extra)
        return self.post("manage_connect", data, http)


class ConnectPageTests(ConnectFixture):
    def test_the_connect_modal_offers_paste_scan_and_image(self):
        body = self.page()
        self.assertIn('data-testid="bucket-connect-open"', body)
        self.assertIn('data-testid="bucket-connect-root"', body)
        # Paste: a textarea no form ever submits (it has no name).
        textarea = re.search(r'<textarea id="bucket-connect-code"[^>]*>', body).group(0)
        self.assertNotIn("name=", textarea)
        # Scan: a button; the camera is asked for only in its handler.
        self.assertIn('data-testid="bucket-connect-scan"', body)
        self.assertIn("Scan QR", body)
        video = re.search(r"<video[^>]*>", body).group(0)
        self.assertNotIn("autoplay", video)
        script = body[body.index("Alpine.data('bucketConnect'"):]
        self.assertIn("getUserMedia", script)
        self.assertLess(script.index("async startScan()"), script.index("getUserMedia"))
        self.assertIn("BarcodeDetector", body)
        self.assertIn("vendor/jsqr/jsQR.js", body)
        self.assertIn("stopScan", script[script.index("close() {"):script.index("close() {") + 80])
        # Image: read in the browser — a file input with no name, in no form.
        image = re.search(r'<input type="file"[^>]*data-testid="bucket-connect-image"[^>]*>', body).group(0)
        self.assertIn('accept="image/*"', image)
        self.assertNotIn("name=", image)
        self.assertIn("never uploaded", body)
        # The four steps.
        for step in ("bucket-connect-step-code", "bucket-connect-step-preview",
                     "bucket-connect-step-test", "bucket-connect-step-name"):
            self.assertIn(f'data-testid="{step}"', body)
        self.assertIn('id="bucket-connect-owner"', body)

    def test_the_page_names_no_secret_of_a_mount(self):
        body = self.page()
        self.assertNotIn(self.peer.magic_token, body)
        self.assertNotIn(str(self.peer.grant_uid), body)


class PreviewTests(ConnectFixture):
    def preview(self, code):
        return self.post("manage_connect_preview", {"pairing_code": code})

    def test_a_code_shows_what_it_grants_before_anything_is_saved(self):
        code, grant, raw = self.exported(may_list=True, may_download=True)
        peers, buckets = BucketPeer.objects.count(), Bucket.objects.count()
        response = self.preview(code)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["preview"]["host"], PEER_HOST)
        self.assertEqual(data["preview"]["bucket"], self.local.slug)
        self.assertEqual(data["preview"]["rights_labels"], ["list", "download"])
        self.assertFalse(data["preview"]["may_delete"])
        self.assertTrue(data["preview"]["expires"])
        self.assertEqual(data["name"], self.local.slug)
        self.assertEqual((BucketPeer.objects.count(), Bucket.objects.count()), (peers, buckets))
        self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_an_old_code_without_address_or_end_date_still_previews(self):
        code, grant, raw = self.exported(host="")
        payload = self.payload(code)
        payload.pop("expires_at")
        old = base64.b64encode(json.dumps(payload).encode()).decode()
        data = self.preview(old).json()
        self.assertTrue(data["ok"])
        self.assertEqual((data["preview"]["host"], data["preview"]["expires"]), ("", ""))

    def test_malformed_codes_say_what_to_do(self):
        code, grant, raw = self.exported()
        payload = self.payload(code)
        v2 = base64.b64encode(json.dumps(dict(payload, v=2)).encode()).decode()
        bad_uid = base64.b64encode(json.dumps(dict(payload, grant_uid="nope")).encode()).decode()
        cases = [("", "Paste the pairing code"), ("not-a-code", "does not decode"),
                 (v2, "Unsupported"), (bad_uid, "does not decode"), ("x" * 5000, "does not decode")]
        for value, sentence in cases:
            with self.subTest(sentence=sentence):
                response = self.preview(value)
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertFalse(data["ok"])
                self.assertEqual(data["step"], "code")
                self.assertIn(sentence, data["error"])
                self.assertIn("pairing_code", data["errors"])
                self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_a_hand_made_code_is_refused_with_a_sentence_at_every_door(self):
        """Odd JSON types or values too long for their columns: a sentence,
        never a crash — at the preview, the test, Connect and Replace."""
        code, grant, raw = self.exported()
        payload = self.payload(code)
        forged = [dict(payload, rights=5), dict(payload, rights=[["may_list"]]),
                  dict(payload, magic_token="t" * 300), dict(payload, api_key=42),
                  dict(payload, bucket={"x": 1}), dict(payload, host=["https://a.example.org"]),
                  dict(payload, bucket="b" * 300)]
        for number, value in enumerate(forged):
            bad = base64.b64encode(json.dumps(value).encode()).decode()
            for door in ("manage_connect_preview", "manage_connect_test", "manage_connect",
                         "manage_connect_renew"):
                with self.subTest(case=number, door=door):
                    response = self.post(door, {"pairing_code": bad, "base_url": PEER_HOST,
                                                "name": f"Forged {number}", "owner": self.member.pk})
                    self.assertEqual(response.status_code, 400)
                    self.assertIn("does not decode", response.json()["error"])
        self.assertFalse(Bucket.objects.filter(name__startswith="Forged").exists())

    def test_an_address_longer_than_its_column_is_refused(self):
        code, grant, raw = self.exported()
        long_url = "https://" + "a" * 190 + ".example.org"
        for door in ("manage_connect_test", "manage_connect"):
            with self.subTest(door=door):
                data = self.post(door, {"pairing_code": code, "base_url": long_url,
                                        "name": "Long", "owner": self.member.pk}).json()
                self.assertEqual(data["step"], "address")
                self.assertIn("at most 200 characters", data["errors"]["base_url"][0])
        response = self.mint(address=long_url)
        self.assertIn("at most 200 characters", response.json()["errors"]["address"][0])

    def test_an_expired_code_is_refused(self):
        code, grant, raw = self.exported(expires_at=timezone.now() - timedelta(minutes=1))
        response = self.preview(code)
        self.assertEqual(response.status_code, 400)
        self.assertIn("expired", response.json()["error"])
        self.assertIn("Ask the other Zenobia", response.json()["error"])
        self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_an_already_connected_code_is_refused_and_says_what_to_do(self):
        code, grant, raw = self.exported()
        peer = BucketPeer(label="Already", base_url=PEER_HOST, grant_uid=grant.grant_uid,
                          magic_token=grant.magic_token)
        peer.set_api_key(raw)
        peer.save()
        data = self.preview(code).json()
        self.assertFalse(data["ok"])
        self.assertIn("already connected here, as 'Already'", data["error"])
        self.assertIn("nothing to do", data["error"])
        self.assertEqual(data["connected"], {"label": "Already", "renewable": False})
        # The same grant with another key (rotated there): replacing is offered.
        new_raw = grant.issue_api_key()
        grant.save()
        rotated = pairing_code_for(grant, new_raw, host=PEER_HOST)
        data = self.preview(rotated).json()
        self.assertEqual(data["connected"], {"label": "Already", "renewable": True})
        self.assertIn("replace the key stored here", data["error"])
        for value in (code, rotated):
            self.assert_no_secret(json.dumps(data), secrets_of(value))

    def test_nothing_goes_to_the_session_or_the_logs(self):
        code, grant, raw = self.exported()
        with captured_logs() as logs:
            self.preview(code)
            self.preview("not-a-code")
            self.post("manage_connect_test", {"pairing_code": code, "base_url": PEER_HOST}, LeakyHttp())
        self.assert_no_secret("\n".join(logs), secrets_of(code))
        self.assert_no_secret(self.session_dump(), secrets_of(code))
        self.assertNotIn(manage_views.DRAFT_KEY, self.client.session)


class ConnectionTestTests(ConnectFixture):
    def test_the_test_answers_and_saves_nothing(self):
        code, grant, raw = self.exported()
        peers = BucketPeer.objects.count()
        response = self.post("manage_connect_test", {"pairing_code": code, "base_url": PEER_HOST + "/"})
        data = response.json()
        self.assertTrue(data["ok"], data)
        self.assertIn(self.local.slug, data["detail"])
        self.assertEqual(data["base_url"], PEER_HOST)
        self.assertEqual(BucketPeer.objects.count(), peers)
        self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_a_failed_test_says_why_without_the_token(self):
        code, grant, raw = self.exported()
        response = self.post("manage_connect_test", {"pairing_code": code, "base_url": PEER_HOST},
                             LeakyHttp())
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertIn("Max retries", data["detail"])
        self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_the_address_is_ssrf_guarded(self):
        code, grant, raw = self.exported()
        response = self.post("manage_connect_test", {"pairing_code": code,
                                                     "base_url": "https://169.254.169.254"}, DownHttp())
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["step"], "address")
        self.assertIn("base_url", data["errors"])
        with mock.patch("toto.vault.outbound.socket.getaddrinfo", private_dns):
            data = self.post("manage_connect_test", {"pairing_code": code,
                                                     "base_url": "https://inside.example.org"}).json()
        self.assertIn("not a public address", data["errors"]["base_url"][0])
        data = self.post("manage_connect_test", {"pairing_code": code, "base_url": "http://peer.example.org"}).json()
        self.assertIn("plain http", data["errors"]["base_url"][0])


class ConnectTests(ConnectFixture):
    def test_a_failed_test_refuses_connect_and_saves_nothing(self):
        code, grant, raw = self.exported()
        peers, buckets = BucketPeer.objects.count(), Bucket.objects.count()
        response = self.connect(code, http=LeakyHttp())
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["ok"])
        self.assertEqual(data["step"], "test")
        self.assertIn("nothing was connected", data["errors"]["test"][0])
        self.assertEqual((BucketPeer.objects.count(), Bucket.objects.count()), (peers, buckets))
        self.assert_no_secret(response.content.decode(), secrets_of(code))
        self.assertNotIn(manage_views.DRAFT_KEY, self.client.session)

    def test_connect_makes_the_pairing_and_the_bucket(self):
        code, grant, raw = self.exported(may_list=True, may_download=True)
        with captured_logs() as logs:
            response = self.connect(code)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {"ok": True, "redirect": reverse("vault:manage")})
        bucket = Bucket.objects.get(name="Mounted Alpha")
        self.assertEqual(bucket.storage_backend, StorageBackend.REMOTE_TOTO)
        self.assertEqual((bucket.owner, bucket.created_by), (self.member, self.root))
        self.assertTrue(bucket.ai_protected)
        peer = bucket.peer
        self.assertEqual(peer.base_url, PEER_HOST)
        self.assertEqual(str(peer.grant_uid), str(grant.grant_uid))
        self.assertEqual(peer.get_api_key(), raw)
        self.assertNotIn(raw.encode(), bytes(peer.api_key_encrypted))
        self.assertEqual(peer.paired_by, self.root)
        self.assertEqual(peer.remote_bucket_slug, self.local.slug)
        peer.refresh_from_db()
        self.assertIsNotNone(peer.last_ok_at)
        self.assertIn("VAULT.BUCKET.CREATED", audit_actions())
        secrets = secrets_of(code)
        self.assert_no_secret(response.content.decode(), secrets)
        self.assert_no_secret(audit_dump(), secrets)
        self.assert_no_secret("\n".join(logs), secrets)
        self.assert_no_secret(self.session_dump(), secrets)
        page = self.as_root().get(reverse("vault:manage")).content.decode()
        self.assertIn("is connected to the other Zenobia", page)
        self.assertIn("Mounted Alpha", page)
        self.assert_no_secret(page, secrets)
        # The same code again: already connected here.
        again = self.post("manage_connect_preview", {"pairing_code": code}).json()
        self.assertIn("already connected here, as 'Mounted Alpha'", again["error"])

    def test_a_bad_name_or_owner_is_named_and_nothing_is_saved(self):
        code, grant, raw = self.exported()
        peers = BucketPeer.objects.count()
        response = self.connect(code, name="Alpha", owner="")
        data = response.json()
        self.assertEqual(response.status_code, 400)
        self.assertEqual(data["step"], "name")
        self.assertIn("name", data["errors"])
        self.assertIn("owner", data["errors"])
        self.assertEqual(BucketPeer.objects.count(), peers)
        self.assert_no_secret(response.content.decode(), secrets_of(code))

    def test_an_expired_or_connected_code_is_refused_at_connect_too(self):
        code, grant, raw = self.exported(expires_at=timezone.now() - timedelta(minutes=1))
        data = self.connect(code).json()
        self.assertEqual(data["step"], "code")
        self.assertIn("expired", data["error"])
        self.assertFalse(Bucket.objects.filter(name="Mounted Alpha").exists())

    def test_a_host_without_a_permanent_key_cannot_connect(self):
        code, grant, raw = self.exported()
        with override_settings(VAULT_FIELD_KEY_PERSISTENT=False), \
                mock.patch.dict(os.environ, {"FIELD_ENCRYPTION_KEY": ""}):
            body = self.page()
            self.assertIn('data-testid="bucket-connect-blocked"', body)
            for door in ("manage_connect_preview", "manage_connect_test", "manage_connect",
                         "manage_connect_renew"):
                with self.subTest(door=door):
                    response = self.post(door, {"pairing_code": code, "base_url": PEER_HOST,
                                                "name": "Mounted Alpha", "owner": self.member.pk})
                    self.assertEqual(response.status_code, 409)
                    self.assertIn("FIELD_ENCRYPTION_KEY", response.json()["error"])
        self.assertFalse(Bucket.objects.filter(name="Mounted Alpha").exists())

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_a_host_without_external_buckets_cannot_connect(self):
        code, grant, raw = self.exported()
        response = self.post("manage_connect_preview", {"pairing_code": code})
        self.assertEqual(response.status_code, 409)
        self.assertIn("External buckets are disabled", response.json()["error"])


class TwoSidedTests(ConnectFixture):
    """The whole flow on the loopback: shared here by the share door, connected
    here by the connect door; the key rotated, then replaced on the
    connecting side."""

    def test_share_connect_rotate_replace(self):
        code = self.code_of(self.mint(label="Loop", may_list="1", may_download="1"))
        grant = BucketGrant.objects.get(label="Loop")
        self.assertTrue(self.post("manage_connect_preview", {"pairing_code": code}).json()["ok"])
        self.assertTrue(self.post("manage_connect_test", {"pairing_code": code,
                                                          "base_url": PEER_HOST}).json()["ok"])
        self.assertTrue(self.connect(code).json()["ok"])
        mount = Bucket.objects.get(name="Mounted Alpha")
        # Rotated on the sharing side: the connection stops working…
        new = self.code_of(self.rotate(grant, expires="keep"))
        with mock.patch("toto.vault.peer_client._http", return_value=LoopbackHttp()):
            with self.assertRaises(Exception):
                PeerClient(BucketPeer.objects.get(pk=mount.peer_id)).manifest()
        # …until the new code replaces the stored key there.
        preview = self.post("manage_connect_preview", {"pairing_code": new}).json()
        self.assertEqual(preview["connected"], {"label": "Mounted Alpha", "renewable": True})
        response = self.post("manage_connect_renew", {"pairing_code": new})
        self.assertEqual(response.json(), {"ok": True, "redirect": reverse("vault:manage")})
        peer = BucketPeer.objects.get(pk=mount.peer_id)
        self.assertEqual(peer.get_api_key(), self.payload(new)["api_key"])
        self.assertEqual(peer.last_error, "")
        with mock.patch("toto.vault.peer_client._http", return_value=LoopbackHttp()):
            self.assertEqual(PeerClient(peer).manifest()["bucket"], self.local.slug)
        self.assertIn("VAULT.BUCKET.PEER_KEY_REPLACED", audit_actions())
        # The same code again: nothing to do.
        again = self.post("manage_connect_renew", {"pairing_code": new}).json()
        self.assertTrue(again["unchanged"])
        for value in (code, new):
            self.assert_no_secret(audit_dump(), secrets_of(value))
            self.assert_no_secret(self.page(), secrets_of(value))
            self.assert_no_secret(self.session_dump(), secrets_of(value))

    def test_a_new_key_the_other_side_refuses_keeps_the_stored_one(self):
        code = self.code_of(self.mint(label="Loop"))
        grant = BucketGrant.objects.get(label="Loop")
        self.assertTrue(self.connect(code).json()["ok"])
        mount = Bucket.objects.get(name="Mounted Alpha")
        new = self.code_of(self.rotate(grant))
        forged = base64.b64encode(json.dumps(dict(self.payload(new), api_key="y" * 64)).encode()).decode()
        response = self.post("manage_connect_renew", {"pairing_code": forged})
        self.assertEqual(response.status_code, 400)
        self.assertIn("did not accept the new key", response.json()["error"])
        self.assertEqual(BucketPeer.objects.get(pk=mount.peer_id).get_api_key(),
                         self.payload(code)["api_key"])
        self.assert_no_secret(response.content.decode(), secrets_of(forged) + secrets_of(new))

    def test_renew_needs_a_connection_that_uses_the_code(self):
        code, grant, raw = self.exported()
        response = self.post("manage_connect_renew", {"pairing_code": code})
        self.assertEqual(response.status_code, 400)
        self.assertIn("connect it as a new bucket", response.json()["error"])


# ---------------------------------------------------------------------------
# Regressions (review of 2026-09-30)
# ---------------------------------------------------------------------------

class _Answer:
    """A requests.Response stand-in."""

    def __init__(self, status, body=None, location=""):
        self.status_code = status
        self.headers = {"Location": location} if location else {}
        self._body = body or {}
        self.closed = False

    def json(self):
        return self._body

    @property
    def text(self):
        return json.dumps(self._body)

    @property
    def content(self):
        return self.text.encode()

    def close(self):
        self.closed = True


class RedirectingPeer:
    """requests' own behaviour, faithfully: a redirect is FOLLOWED unless
    ``allow_redirects=False`` — headers and all (requests strips only
    Authorization across hosts, never X-Vault-Api-Key). The "peer" answers
    every call with a redirect to an internal address, whose own answer must
    never be read."""

    INTERNAL = "http://127.0.0.1:8080/latest/meta-data/iam"

    def __init__(self, status=302):
        self.status = status
        self.calls = []

    def request(self, method, url, headers=None, allow_redirects=True, **kwargs):
        self.calls.append((method, url, dict(headers or {})))
        if url.startswith(self.INTERNAL):
            return _Answer(403, {"error": "SECRET-INTERNAL-BODY role=admin"})
        if allow_redirects:
            return self.request(method, self.INTERNAL, headers=headers, **kwargs)
        return _Answer(self.status, location=self.INTERNAL)

    def internal_calls(self):
        return [c for c in self.calls if c[1].startswith(self.INTERNAL)]


class RedirectTests(ConnectFixture):
    """The SSRF guard checks the address it is given, once; a redirect must
    never take the next request (and the api key header) past it."""

    def test_the_client_never_follows_a_redirect(self):
        import io

        from .peer_client import PeerError

        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                transport = RedirectingPeer(status)
                client = PeerClient(self.peer, api_key="api-key-0123456789")
                with mock.patch("toto.vault.peer_client._http", return_value=transport):
                    with self.assertRaises(PeerError) as caught:
                        client.manifest()
                    with self.assertRaises(PeerError):
                        client.upload(io.BytesIO(b"x"), "x.txt")
                    with self.assertRaises(PeerError):
                        client.delete("some-key")
                self.assertEqual(len(transport.calls), 3)
                self.assertEqual(transport.internal_calls(), [])
                self.assertIn("redirect", str(caught.exception))
                self.assertNotIn("127.0.0.1", str(caught.exception))
                self.assertNotIn("SECRET-INTERNAL-BODY", str(caught.exception))
                self.peer.refresh_from_db()
                self.assertIn("redirect", self.peer.last_error)
                self.assertNotIn("SECRET-INTERNAL-BODY", self.peer.last_error)

    def test_a_caller_cannot_turn_redirects_back_on(self):
        transport = RedirectingPeer()
        client = PeerClient(self.peer, api_key="api-key-0123456789")
        with mock.patch("toto.vault.peer_client._http", return_value=transport):
            with self.assertRaises(Exception):
                client._request("GET", "manifest/", allow_redirects=True)
        self.assertEqual(transport.internal_calls(), [])

    def test_the_connection_test_door_does_not_follow_a_redirect(self):
        code, grant, raw = self.exported()
        transport = RedirectingPeer()
        for door, extra in (("manage_connect_test", {}),
                            ("manage_connect", {"name": "Redirected", "owner": self.member.pk})):
            with self.subTest(door=door):
                response = self.post(door, {"pairing_code": code, "base_url": PEER_HOST, **extra},
                                     transport)
                body = response.content.decode()
                self.assertFalse(response.json()["ok"])
                self.assertIn("redirect", body)
                self.assertNotIn("SECRET-INTERNAL-BODY", body)
                self.assertNotIn("127.0.0.1", body)
                self.assert_no_secret(body, secrets_of(code))
        self.assertEqual(transport.internal_calls(), [])
        self.assertFalse(Bucket.objects.filter(name="Redirected").exists())

    def test_the_test_button_of_a_mount_does_not_follow_a_redirect(self):
        transport = RedirectingPeer()
        with mock.patch("toto.vault.peer_client._http", return_value=transport):
            response = self.as_root().post(reverse("vault:manage_test", args=[self.mount.pk]))
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual(transport.internal_calls(), [])
        self.assertNotIn("SECRET-INTERNAL-BODY", response.content.decode())
        self.peer.refresh_from_db()
        self.assertNotIn("SECRET-INTERNAL-BODY", self.peer.last_error)
        self.assertNotIn("SECRET-INTERNAL-BODY", self.page())


class ListRightTests(ConnectFixture):
    """Connecting reads the share's manifest, which needs List: a share
    without it was a live credential nobody could ever connect."""

    def test_a_share_without_list_is_refused_and_says_what_to_tick(self):
        for rights in ({"may_download": "1"}, {"may_upload": "1"}, {"may_delete": "1"},
                       {"may_download": "1", "may_upload": "1", "may_delete": "1"}):
            with self.subTest(rights=rights):
                response = self.mint(may_list="0", **rights)
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertIn("Tick List too", data["errors"]["rights"][0])
        self.assertFalse(BucketGrant.objects.exists())
        self.assertEqual(self.mint(may_list="1", may_download="1").status_code, 200)

    def test_the_form_ticks_list_with_any_other_right(self):
        body = self.page()
        for right in ("may_download", "may_upload", "may_delete"):
            tag = re.search(rf'<input type="checkbox" name="{right}"[^>]*>', body).group(0)
            with self.subTest(right=right):
                self.assertIn('@change="needList($event)"', tag)
        self.assertIn("needList(event) {", body)
        self.assertIn("Needed to connect the bucket there", body)

    def test_a_code_without_list_is_refused_at_every_connect_door_with_the_reason(self):
        code, grant, raw = self.exported(may_download=True)
        for door in ("manage_connect_preview", "manage_connect_test", "manage_connect"):
            with self.subTest(door=door):
                response = self.post(door, {"pairing_code": code, "base_url": PEER_HOST,
                                            "name": "No List", "owner": self.member.pk})
                self.assertEqual(response.status_code, 400)
                data = response.json()
                self.assertEqual(data["step"], "code")
                self.assertIn("does not include List", data["error"])
                self.assert_no_secret(response.content.decode(), secrets_of(code))
        self.assertFalse(Bucket.objects.filter(name="No List").exists())


# ---------------------------------------------------------------------------
# The modals' own behaviour, run in node against the page's real scripts
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")

_HARNESS = r"""
const ISLANDS = __ISLANDS__;
const listeners = {}, factories = {}, streams = [], out = {};
globalThis.window = globalThis;
Object.defineProperty(globalThis, 'isSecureContext', {value: true, configurable: true, writable: true});
globalThis.addEventListener = () => {};
globalThis.document = {
  addEventListener(name, fn) { (listeners[name] = listeners[name] || []).push(fn); },
  getElementById(id) { return Object.prototype.hasOwnProperty.call(ISLANDS, id) ? {textContent: ISLANDS[id]} : null; },
  createElement() { return {getContext() { return null; }}; },
  head: {appendChild() {}},
  hidden: false,
};
globalThis.Alpine = {data(name, fn) { factories[name] = fn; }};
globalThis.jsQR = () => null;
Object.defineProperty(globalThis, 'navigator', {configurable: true, writable: true, value: {
  mediaDevices: {getUserMedia() {
    return new Promise((resolve) => setTimeout(() => {
      const track = {state: 'live', stop() { this.state = 'ended'; }};
      const s = {getTracks: () => [track], track};
      streams.push(s); resolve(s);
    }, 30));
  }},
}});
const answers = {};
globalThis.fetch = async (url, opts) => {
  const fn = answers[url];
  const a = fn ? fn(opts && opts.body) : {status: 404, json: {ok: false}};
  const status = a.status || 200;
  return {ok: status < 400, status,
          headers: {get: () => (a.html !== undefined ? 'text/html; charset=utf-8' : 'application/json')},
          json: async () => a.json, text: async () => a.html};
};
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 0));
const video = () => ({setAttribute() {}, play: async () => {}, pause() {}, readyState: 0, srcObject: null});
function mount(name, ...args) {
  const c = factories[name](...args);
  c.$el = {dataset: {csrf: 't'}};
  c.$refs = {};
  c.$nextTick = (fn) => Promise.resolve().then(() => fn && fn());
  if (c.init) c.init();
  return c;
}
const live = () => streams.filter((s) => s.track.state === 'live').length;

__SCRIPTS__

(async () => {
  (listeners['alpine:init'] || []).forEach((fn) => fn());
  const cfg = JSON.parse(ISLANDS['bucket-share-config']);

  // Two clicks on Scan QR before the camera answers: one camera, and Stop stops it.
  let c = mount('bucketConnect'); c.$refs.video = video(); c.open = true; c.step = 1;
  await Promise.all([c.startScan(), c.startScan()]);
  const opened = streams.length;
  c.stopScan();
  out.double = {opened, live: live()};

  // Stop while the browser is still asking: the camera that arrives is stopped at once.
  streams.length = 0;
  c = mount('bucketConnect'); c.$refs.video = video(); c.open = true; c.step = 1;
  const pending = c.startScan();
  await tick(5);
  c.stopScan();
  await pending;
  out.stopped = {live: live(), scanning: c.scanning, starting: c.starting};

  // Back, then a different code: its own address and name, not the first one's.
  const preview = (host, bucket) => ({json: {ok: true, name: bucket, preview: {
    host, bucket, rights: ['may_list'], rights_labels: ['list'], may_delete: false, expires: ''}}});
  answers[cfg.urls.preview] = (body) => (body.get('pairing_code') === 'CODE-A'
    ? preview('https://a.example.org', 'alpha') : preview('https://b.example.org', 'beta'));
  c = mount('bucketConnect'); c.open = true;
  c.code = 'CODE-A'; await c.toPreview();
  out.first = {baseUrl: c.baseUrl, name: c.name};
  c.go(1); c.code = 'CODE-B'; await c.toPreview();
  out.second = {baseUrl: c.baseUrl, name: c.name, step: c.step};
  c.go(1); c.baseUrl = 'https://typed.example.org'; c.name = 'Mine'; c.code = 'CODE-A'; await c.toPreview();
  out.typed = {baseUrl: c.baseUrl, name: c.name};

  // An already-connected code: the panel only for a key that can be replaced.
  for (const renewable of [false, true]) {
    answers[cfg.urls.preview] = () => ({status: 400, json: {ok: false, error: 'x', errors: {}, step: 'code',
                                                            connected: {label: 'Already', renewable}}});
    c = mount('bucketConnect'); c.open = true; c.code = 'X'; await c.toPreview();
    out['renew_' + renewable] = c.renew;
  }

  // The page's forms post once.
  const m = mount('bucketManage', '/people/');
  const ev = () => ({prevented: false, preventDefault() { this.prevented = true; }});
  const e1 = ev(), e2 = ev();
  m.guard(e1); m.guard(e2);
  out.guard = {first: e1.prevented, second: e2.prevented, submitting: m.submitting};

  // The share modal moves focus into itself, onto Rotate's first field, and onto Copy.
  const focused = [];
  const s = mount('bucketShare');
  s.$refs.shareTitle = {focus() { focused.push('title'); }};
  s.$refs.rotateExpires = {focus() { focused.push('rotate'); }};
  const copy = {focus() { focused.push('copy'); }};
  s.$refs.result = {innerHTML: '', querySelector(sel) { return sel === '[data-copy-code]' ? copy : null; }};
  answers['/list/'] = () => ({json: {ok: true, bucket: {pk: 1, name: 'A'}, shares: [], refusal: '', share_url: '/share/'}});
  answers['/share/'] = () => ({html: '<div>the code</div>'});
  await s.openFor({url: '/list/'});
  s.startRotate({status: 'active', label: 'x', rotate_url: '/share/'});
  await tick();
  await s.mint('/share/', undefined);
  await tick();
  out.focus = focused;
  s.form.may_list = false; s.needList({target: {checked: true}});
  out.needList = s.form.may_list;

  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})().catch((e) => { console.error(e && e.stack || e); process.exit(2); });
"""


@skipUnless(_NODE, "node is not installed")
class ModalBehaviourTests(ConnectFixture):
    """The Alpine components exactly as the page ships them, in node, with the
    browser's camera, fetch and focus faked."""

    @classmethod
    def run_harness(cls, body):
        islands = dict(re.findall(r'<script id="([^"]+)" type="application/json">(.*?)</script>', body, re.S))
        scripts = [s for s in re.findall(r"<script>(.*?)</script>", body, re.S)
                   if "Alpine.data('bucket" in s]
        program = (_HARNESS.replace("__ISLANDS__", json.dumps(islands))
                   .replace("__SCRIPTS__", "\n".join(scripts)))
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(program)
        try:
            done = subprocess.run([_NODE, handle.name], capture_output=True, text=True, timeout=60)
        finally:
            os.unlink(handle.name)
        if done.returncode != 0:
            raise AssertionError(f"node failed: {done.stderr}")
        return json.loads(done.stdout)

    #: One node run for the class: every test reads its own part.
    _out = None

    def setUp(self):
        super().setUp()
        if ModalBehaviourTests._out is None:
            ModalBehaviourTests._out = self.run_harness(self.page())
        self.out = ModalBehaviourTests._out

    def test_two_clicks_on_scan_open_one_camera_and_stop_stops_it(self):
        self.assertEqual(self.out["double"], {"opened": 1, "live": 0})

    def test_stop_while_the_browser_asks_leaves_no_camera_on(self):
        self.assertEqual(self.out["stopped"], {"live": 0, "scanning": False, "starting": False})

    def test_a_different_code_after_back_brings_its_own_address_and_name(self):
        self.assertEqual(self.out["first"], {"baseUrl": "https://a.example.org", "name": "alpha"})
        self.assertEqual(self.out["second"], {"baseUrl": "https://b.example.org", "name": "beta", "step": 2})
        self.assertEqual(self.out["typed"], {"baseUrl": "https://typed.example.org", "name": "Mine"})

    def test_the_renew_panel_only_for_a_key_that_can_be_replaced(self):
        self.assertIsNone(self.out["renew_false"])
        self.assertEqual(self.out["renew_true"], {"label": "Already", "renewable": True})
        self.assertIn('x-show="renew && renew.renewable"', self.page())

    def test_a_form_of_the_page_posts_once(self):
        self.assertEqual(self.out["guard"], {"first": False, "second": True, "submitting": True})

    def test_the_share_modal_moves_focus_into_itself(self):
        self.assertEqual(self.out["focus"], ["title", "rotate", "copy"])
        self.assertTrue(self.out["needList"])
