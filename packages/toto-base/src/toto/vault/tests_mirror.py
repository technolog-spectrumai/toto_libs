"""The metadata mirror and the peer client, wired through a loopback.

The two-host harness is clearing's one-DB pattern: ``peer_client._http`` is
patched with a shim that routes every request into Django's test client
against the REAL peer views, so the exporter and the mounting host share one
database and the whole path — driver → client → HTTP shapes → server →
storage — is exercised without a socket.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_mirror
"""
import io
import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from . import mirror
from .models import Bucket, FileOrigin, StorageBackend, VaultFile
from .peering import BucketGrant, BucketPeer

User = get_user_model()


class _Raw(io.BytesIO):
    """BytesIO that tolerates requests' ``decode_content`` attribute."""


class LoopbackResponse:
    def __init__(self, resp):
        self._resp = resp
        self.status_code = resp.status_code
        self._content = None
        self._raw = None

    @property
    def content(self) -> bytes:
        # Memoized: streaming_content is a generator, and requests' .content
        # is stable across reads — the shim must be too.
        if self._content is None:
            if getattr(self._resp, "streaming", False):
                self._content = b"".join(self._resp.streaming_content)
            else:
                self._content = self._resp.content
        return self._content

    @property
    def text(self) -> str:
        return self.content.decode(errors="replace")

    @property
    def raw(self):
        if self._raw is None:
            self._raw = _Raw(self.content)
        return self._raw

    def json(self):
        return json.loads(self.content)


class LoopbackHttp:
    """The requests-module stand-in: every call becomes a test-client call."""

    def __init__(self):
        self.client = Client()
        self.calls = []

    def request(self, method, url, headers=None, timeout=None, stream=False,
                params=None, files=None, **kwargs):
        from urllib.parse import urlencode, urlsplit

        self.calls.append((method, url))
        path = urlsplit(url).path
        if params:
            path += "?" + urlencode(params)
        extra = {("HTTP_" + k.upper().replace("-", "_")): v
                 for k, v in (headers or {}).items()}
        if method == "GET":
            resp = self.client.get(path, **extra)
        elif method == "HEAD":
            resp = self.client.head(path, **extra)
        elif method == "DELETE":
            resp = self.client.delete(path, **extra)
        elif method == "POST":
            data = {}
            if files:
                field, (filename, fileobj) = next(iter(files.items()))
                data[field] = SimpleUploadedFile(filename, fileobj.read())
            resp = self.client.post(path, data, **extra)
        else:  # pragma: no cover - no other verbs exist on the wire
            raise AssertionError(method)
        return LoopbackResponse(resp)


class DownHttp:
    """A peer that is off the network entirely."""

    def request(self, *args, **kwargs):
        raise ConnectionError("connection refused")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), VAULT_EXTERNAL_BUCKETS=True)
class MirrorTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("mounter", password="x")
        cls.exporter = User.objects.create_user("exporter", password="x")
        cls.source = Bucket.objects.create(
            name="source", slug="source", owner=cls.exporter)
        cls.grant = BucketGrant.objects.create(
            label="loopback", bucket=cls.source,
            may_list=True, may_download=True)
        raw_key = cls.grant.issue_api_key()
        cls.grant.save()
        cls.peer = BucketPeer.objects.create(
            label="Peerhost", base_url="https://peer.example.org",
            grant_uid=cls.grant.grant_uid, magic_token=cls.grant.magic_token,
            remote_bucket_slug="source")
        cls.peer.set_api_key(raw_key)
        cls.peer.save()
        cls.mounted = Bucket.objects.create(
            name="mounted", slug="mounted", owner=cls.owner,
            storage_backend=StorageBackend.REMOTE_TOTO, peer=cls.peer)

    def _source_file(self, key="doc", content=b"remote bytes"):
        vf = VaultFile(owner=self.exporter, title=f"{key}.txt", key=key,
                       file_type="text", bucket=self.source)
        vf.file.save(f"{key}.txt", SimpleUploadedFile(f"{key}.txt", content),
                     save=True)
        return vf

    def _refresh(self, http=None):
        run = mirror.BucketRefreshRun.objects.create(
            bucket=self.mounted, owner=self.owner)
        with mock.patch("toto.vault.peer_client._http",
                        return_value=http or LoopbackHttp()):
            return mirror.execute_refresh_run(run.pk)


class RefreshDiffTests(MirrorTestCase):
    def test_first_refresh_creates_stubs(self):
        self._source_file("doc-a", b"aaa")
        self._source_file("doc-b", b"bbbb")
        run = self._refresh()
        self.assertEqual(run.status, mirror.RefreshStatus.SUCCESS, run.error)
        self.assertEqual(run.stubs_created, 2)
        self.assertEqual(run.total_remote, 2)
        self.assertGreaterEqual(run.pages_read, 1)
        stub = VaultFile.objects.get(bucket=self.mounted, key="doc-a")
        self.assertEqual(stub.origin, FileOrigin.MIRROR)
        self.assertEqual(stub.owner, self.owner)
        self.assertFalse(stub.is_public)
        # The wire name IS the remote key — that is what the driver
        # dereferences on download.
        self.assertEqual(stub.file.name, "doc-a")
        self.assertEqual(stub.file_size_bytes, 3)
        self.mounted.refresh_from_db()
        self.assertIsNotNone(self.mounted.last_refreshed_at)
        self.peer.refresh_from_db()
        self.assertIsNotNone(self.peer.last_ok_at)
        self.assertEqual(self.peer.pull_count, 1)

    def test_second_refresh_is_all_unchanged(self):
        self._source_file()
        self._refresh()
        run = self._refresh()
        self.assertEqual(run.stubs_unchanged, 1)
        self.assertEqual(run.stubs_created, 0)
        self.assertEqual(run.stubs_updated, 0)

    def test_remote_change_updates_the_stub(self):
        vf = self._source_file()
        self._refresh()
        VaultFile.objects.filter(pk=vf.pk).update(title="renamed.txt")
        run = self._refresh()
        self.assertEqual(run.stubs_updated, 1)
        stub = VaultFile.objects.get(bucket=self.mounted, key="doc")
        self.assertEqual(stub.title, "renamed.txt")

    def test_remote_deletion_prunes_rows_only(self):
        vf = self._source_file()
        native = VaultFile.objects.create(
            owner=self.owner, title="mine", key="mine", file_type="text",
            bucket=self.mounted)
        self._refresh()
        vf.delete()
        run = self._refresh()
        self.assertEqual(run.stubs_deleted, 1)
        self.assertFalse(
            VaultFile.objects.filter(bucket=self.mounted, key="doc").exists())
        # The native row in the same bucket is never the mirror's to prune.
        self.assertTrue(
            VaultFile.objects.filter(pk=native.pk).exists())

    def test_native_key_collision_is_skipped_not_overwritten(self):
        self._source_file("clash", b"remote")
        VaultFile.objects.create(
            owner=self.owner, title="local truth", key="clash",
            file_type="text", bucket=self.mounted)
        run = self._refresh()
        self.assertEqual(run.stubs_created, 0)
        self.assertEqual(len(run.skips), 1)
        self.assertEqual(run.skips[0]["key"], "clash")
        row = VaultFile.objects.get(bucket=self.mounted, key="clash")
        self.assertEqual(row.title, "local truth")
        self.assertEqual(row.origin, FileOrigin.NATIVE)

    def test_peer_down_fails_the_run_and_keeps_stubs(self):
        self._source_file()
        self._refresh()
        run = self._refresh(http=DownHttp())
        self.assertEqual(run.status, mirror.RefreshStatus.FAILED)
        self.assertIn("ConnectionError", run.error)
        self.assertTrue(
            VaultFile.objects.filter(bucket=self.mounted, key="doc").exists())
        self.peer.refresh_from_db()
        self.assertIn("ConnectionError", self.peer.last_error)


class RemoteReadPathTests(MirrorTestCase):
    def test_download_view_streams_remote_bytes_end_to_end(self):
        # The flagship path: download view → driver → PeerClient → loopback
        # HTTP → peer view → exporter's storage. One assertion, whole wire.
        self._source_file("doc", b"the remote payload")
        self._refresh()
        self.client.force_login(self.owner)
        with mock.patch("toto.vault.peer_client._http",
                        return_value=LoopbackHttp()):
            resp = self.client.get(
                reverse("vault:public_file", args=["mounted", "doc"]))
            self.assertEqual(resp.status_code, 200)
            body = b"".join(resp.streaming_content)
        self.assertEqual(body, b"the remote payload")

    def test_dead_peer_download_is_a_502(self):
        self._source_file()
        self._refresh()
        self.client.force_login(self.owner)
        with mock.patch("toto.vault.peer_client._http",
                        return_value=DownHttp()):
            resp = self.client.get(
                reverse("vault:public_file", args=["mounted", "doc"]))
        self.assertEqual(resp.status_code, 502)
        self.assertIn("mounted", resp.content.decode())


class MirrorMetadataLockTests(MirrorTestCase):
    def _stub(self):
        self._source_file()
        self._refresh()
        return VaultFile.objects.get(bucket=self.mounted, key="doc")

    def test_rename_move_delete_refuse_mirror_rows(self):
        stub = self._stub()
        self.client.force_login(self.owner)
        for url, data in (
                (reverse("vault:rename_file"),
                 {"file_pk": stub.pk, "title": "new"}),
                (reverse("vault:move_file"), {"file_pk": stub.pk}),
                (reverse("vault:delete_file"), {"file_pk": stub.pk}),
        ):
            resp = self.client.post(url, data)
            self.assertEqual(resp.status_code, 403, url)
            self.assertIn("origin host", resp.json()["error"])
        self.assertTrue(VaultFile.objects.filter(pk=stub.pk).exists())


class LevyExclusionTests(MirrorTestCase):
    def test_mirror_rows_are_not_billed_but_native_rows_are(self):
        from .taxes import StorageLevy

        self._source_file("doc", b"12345678")
        self._refresh()
        VaultFile.objects.create(
            owner=self.owner, title="mine", key="mine", file_type="text",
            bucket=None, file_size_bytes=100)
        s3 = Bucket.objects.create(
            name="s3", slug="s3b", owner=self.owner, storage_backend="s3",
            storage_config={"bucket_name": "x"})
        VaultFile.objects.create(
            owner=self.owner, title="cloud", key="cloud", file_type="text",
            bucket=s3, file_size_bytes=40)
        levy = StorageLevy()
        # 100 local + 40 s3; the 8-byte mirror stub is the peer's gigabyte.
        self.assertEqual(levy.measure(self.owner), 140)
        sampled = dict(levy.sample())
        self.assertEqual(sampled.get(self.owner.pk), 140)


class RefreshDoorTests(MirrorTestCase):
    def test_refresh_is_owner_or_superuser_404(self):
        stranger = User.objects.create_user("stranger", password="x")
        self.client.force_login(stranger)
        resp = self.client.post(
            reverse("vault:bucket_refresh", args=["mounted"]))
        self.assertEqual(resp.status_code, 404)

    def test_local_bucket_answers_400(self):
        local = Bucket.objects.create(
            name="plain", slug="plain", owner=self.owner)
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:bucket_refresh", args=["plain"]))
        self.assertEqual(resp.status_code, 400)

    def test_no_worker_is_a_503_with_a_failed_row(self):
        # zenobia installs toto.workflows but tests run with no celery
        # worker listening — the refuse-don't-inline branch, by name.
        self.client.force_login(self.owner)
        resp = self.client.post(
            reverse("vault:bucket_refresh", args=["mounted"]))
        self.assertEqual(resp.status_code, 503)
        self.assertIn("worker", resp.json()["error"].lower())
        run = mirror.BucketRefreshRun.objects.get(bucket=self.mounted)
        self.assertEqual(run.status, mirror.RefreshStatus.FAILED)

    def test_status_view_guards_like_the_dispatch_door(self):
        run = mirror.BucketRefreshRun.objects.create(
            bucket=self.mounted, owner=self.owner)
        stranger = User.objects.create_user("stranger2", password="x")
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(
            reverse("vault:bucket_refresh_status", args=[run.pk])
        ).status_code, 404)
        self.client.force_login(self.owner)
        payload = self.client.get(
            reverse("vault:bucket_refresh_status", args=[run.pk])).json()
        self.assertEqual(payload["status"], "pending")
        self.assertIsNone(payload["total_remote"])


class MountConfigTests(MirrorTestCase):
    def test_bucket_clean_requires_an_active_peer(self):
        nopeer = Bucket(name="n", slug="n", owner=self.owner,
                        storage_backend=StorageBackend.REMOTE_TOTO)
        with self.assertRaises(ValidationError):
            nopeer.clean()
        BucketPeer.objects.filter(pk=self.peer.pk).update(is_active=False)
        self.mounted.refresh_from_db()
        with self.assertRaises(ValidationError):
            self.mounted.clean()

    def test_driver_factory_refuses_a_peerless_mount(self):
        from .storage_backends import get_bucket_storage

        rogue = Bucket.objects.create(
            name="rogue", slug="rogue", owner=self.owner,
            storage_backend=StorageBackend.REMOTE_TOTO)
        with self.assertRaisesMessage(RuntimeError, "no bucket peer"):
            get_bucket_storage(rogue)

    def test_connection_url_is_display_only_and_leak_free(self):
        # from the peer row, never from storage_config; and importing it back
        # yields an EMPTY storage_config — a pasted URL can no longer mint a
        # transport identity.
        from .connection import BucketConnectionSpec

        spec = BucketConnectionSpec.from_bucket(self.mounted)
        url = spec.to_url()
        self.assertEqual(url,
                         "toto://peer.example.org/vault/buckets/source/")
        self.assertEqual(BucketConnectionSpec.from_url(url).to_storage_config(),
                         {})

    def test_http_peer_round_trips_its_scheme(self):
        from .connection import BucketConnectionSpec

        spec = BucketConnectionSpec(
            backend="remote_toto", bucket_name="b",
            server_url="http://dev.local:8085")
        url = spec.to_url()
        self.assertTrue(url.startswith("toto+http://"))
        self.assertEqual(BucketConnectionSpec.from_url(url).server_url,
                         "http://dev.local:8085")
