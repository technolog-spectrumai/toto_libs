"""The peer API server half — auth order, capability matrix, wire shapes.

Run only where a gate stanza names this module:

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.vault.tests_peer_api
"""
import json
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import scanning as _scanning
from .models import Bucket, VaultFile
from .peering import BucketGrant

User = get_user_model()

KEY_HEADER = "X-Vault-Api-Key"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class PeerApiTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("exporter", password="x")
        cls.bucket = Bucket.objects.create(
            name="exported", slug="exported", owner=cls.owner)
        cls.grant = BucketGrant.objects.create(
            label="peer", bucket=cls.bucket,
            may_list=True, may_download=True, may_upload=True, may_delete=True)
        cls.raw_key = cls.grant.issue_api_key()
        cls.grant.save()

    def _url(self, name, **extra):
        kwargs = {"grant_uid": self.grant.grant_uid,
                  "magic_token": self.grant.magic_token}
        kwargs.update(extra)
        return reverse(f"vault:{name}", kwargs=kwargs)

    def _get(self, name, key=None, query="", **extra):
        url = self._url(name, **({"key": key} if key else {}))
        return self.client.get(url + query, **{
            "HTTP_X_VAULT_API_KEY": extra.pop("api_key", self.raw_key), **extra})

    def _file(self, key="doc", content=b"hello bytes", **kw):
        vf = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                       file_type="text", bucket=self.bucket, **kw)
        vf.file.save(f"{key}.txt", SimpleUploadedFile(f"{key}.txt", content),
                     save=True)
        return vf


class AuthOrderTests(PeerApiTestCase):
    def test_unknown_pair_is_a_404_with_zero_pbkdf2(self):
        # Anti-enumeration AND the cheap-check order in one: a guessed URL is
        # indistinguishable from one that never existed, and it never reaches
        # key stretching.
        url = self._url("peer_manifest").replace(
            self.grant.magic_token, "guessed-token")
        with mock.patch("django.contrib.auth.hashers.check_password") as chk:
            resp = self.client.get(url, HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 404)
        chk.assert_not_called()

    def test_expired_grant_answers_with_a_sentence(self):
        BucketGrant.objects.filter(pk=self.grant.pk).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1))
        resp = self._get("peer_manifest")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("expired or revoked", resp.content.decode())

    def test_revoked_grant_answers_403(self):
        BucketGrant.objects.filter(pk=self.grant.pk).update(is_active=False)
        self.assertEqual(self._get("peer_manifest").status_code, 403)

    def test_wrong_api_key_answers_403(self):
        resp = self._get("peer_manifest", api_key="wrong-key")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("api key", resp.content.decode())

    def test_missing_api_key_answers_403(self):
        url = self._url("peer_manifest")
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_use_stamps_audit_via_update(self):
        self.assertEqual(self._get("peer_manifest").status_code, 200)
        self.assertEqual(self._get("peer_manifest").status_code, 200)
        stored = BucketGrant.objects.get(pk=self.grant.pk)
        self.assertEqual(stored.read_count, 2)
        self.assertIsNotNone(stored.last_read_at)
        self.assertEqual(stored.last_peer_ip, "127.0.0.1")

    @override_settings(TRUSTED_PROXIES=["172.16.0.0/12"])
    def test_the_peer_address_is_nginx_s_real_ip_not_the_proxy(self):
        """Behind nginx REMOTE_ADDR is the proxy; a forged X-Forwarded-For
        names nobody (2026-09-30)."""
        resp = self._get("peer_manifest", REMOTE_ADDR="172.18.0.5",
                         HTTP_X_REAL_IP="203.0.113.7",
                         HTTP_X_FORWARDED_FOR="198.51.100.66, 203.0.113.7")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(BucketGrant.objects.get(pk=self.grant.pk).last_peer_ip,
                         "203.0.113.7")
        self._get("peer_manifest", REMOTE_ADDR="192.0.2.9",
                  HTTP_X_REAL_IP="203.0.113.7")
        self.assertEqual(BucketGrant.objects.get(pk=self.grant.pk).last_peer_ip,
                         "192.0.2.9")

    def test_urls_match_the_peer_path_constant(self):
        # The client builds URLs from PEER_PATH; a drift between urls.py and
        # the constant must fail here, not in production.
        self.assertTrue(self._url("peer_manifest").startswith(
            self.grant.base_path()))


class CapabilityMatrixTests(PeerApiTestCase):
    def _strip_rights(self):
        BucketGrant.objects.filter(pk=self.grant.pk).update(
            may_list=False, may_download=False, may_upload=False,
            may_delete=False)

    def test_rightless_grant_authenticates_and_can_do_nothing(self):
        self._file()
        self._strip_rights()
        self.assertEqual(self._get("peer_manifest").status_code, 403)
        self.assertEqual(self._get("peer_files").status_code, 403)
        self.assertEqual(
            self._get("peer_file_detail", key="doc").status_code, 403)
        self.assertEqual(
            self._get("peer_file_download", key="doc").status_code, 403)
        resp = self.client.post(self._url("peer_files"),
                                {"file": SimpleUploadedFile("a.txt", b"a")},
                                HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 403)
        resp = self.client.delete(self._url("peer_file_detail", key="doc"),
                                  HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 403)

    def test_delete_needs_may_delete_not_just_may_list(self):
        self._file()
        BucketGrant.objects.filter(pk=self.grant.pk).update(may_delete=False)
        resp = self.client.delete(self._url("peer_file_detail", key="doc"),
                                  HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 403)
        self.assertIn("may_delete", json.loads(resp.content)["error"])


class ListingTests(PeerApiTestCase):
    def test_manifest_names_bucket_and_rights(self):
        self._file()
        data = json.loads(self._get("peer_manifest").content)
        self.assertEqual(data["bucket"], "exported")
        self.assertEqual(data["rights"],
                         ["may_list", "may_download", "may_upload",
                          "may_delete"])
        self.assertEqual(data["total_files"], 1)

    def test_listing_rows_carry_the_mirror_fields(self):
        vf = self._file()
        data = json.loads(self._get("peer_files").content)
        self.assertEqual(data["total"], 1)
        self.assertIsNone(data["next_cursor"])
        row = data["files"][0]
        self.assertEqual(row["key"], "doc")
        self.assertEqual(row["title"], "doc.txt")
        self.assertEqual(row["file_type"], "text")
        self.assertEqual(row["size"], vf.file_size_bytes)
        self.assertFalse(row["is_encrypted"])
        self.assertIn("uploaded_at", row)
        self.assertIn("hash", row)

    def test_keyset_paging_walks_every_row_exactly_once(self):
        for i in range(5):
            self._file(key=f"doc-{i}")
        seen, cursor = [], 0
        for _ in range(10):
            data = json.loads(self._get(
                "peer_files", query=f"?cursor={cursor}&page_size=2").content)
            seen.extend(r["key"] for r in data["files"])
            self.assertEqual(data["total"], 5)
            if data["next_cursor"] is None:
                break
            cursor = data["next_cursor"]
        self.assertEqual(sorted(seen), [f"doc-{i}" for i in range(5)])

    def test_garbage_cursor_is_a_400(self):
        resp = self._get("peer_files", query="?cursor=abc")
        self.assertEqual(resp.status_code, 400)


class DownloadTests(PeerApiTestCase):
    def test_download_streams_the_bytes(self):
        self._file(content=b"the actual bytes")
        resp = self._get("peer_file_download", key="doc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(b"".join(resp.streaming_content),
                         b"the actual bytes")

    def test_head_answers_size_and_hash_without_a_body(self):
        vf = self._file(content=b"12345")
        vf.content_hash = vf.create_hash()
        vf.save()
        resp = self.client.head(self._url("peer_file_download", key="doc"),
                                HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Length"], "5")
        self.assertEqual(resp["X-Vault-Hash"], vf.content_hash)

    def test_encrypted_non_pdf_is_a_409(self):
        # Fernet under THIS host's salt: the ciphertext is garbage anywhere
        # else, so it never crosses.
        self._file(key="sealed", is_encrypted=True)
        resp = self._get("peer_file_download", key="sealed")
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(json.loads(resp.content)["reason"],
                         "encrypted-non-portable")

    def test_encrypted_pdf_crosses(self):
        # PDF password protection is portable standard encryption.
        vf = self._file(key="sealed-pdf", is_encrypted=True)
        VaultFile.objects.filter(pk=vf.pk).update(file_type="pdf")
        resp = self._get("peer_file_download", key="sealed-pdf")
        self.assertEqual(resp.status_code, 200)

    def test_missing_file_is_a_404(self):
        self.assertEqual(
            self._get("peer_file_download", key="ghost").status_code, 404)

    def test_dead_backend_is_a_502_naming_the_bucket(self):
        self._file()
        with mock.patch(
                "toto.vault.storage_backends.open_file_stream",
                side_effect=RuntimeError("network is down")):
            resp = self._get("peer_file_download", key="doc")
        self.assertEqual(resp.status_code, 502)
        body = resp.content.decode()
        self.assertIn("exported", body)
        self.assertIn("network is down", body)


class UploadTests(PeerApiTestCase):
    def _post(self, name="a.txt", content=b"abc"):
        return self.client.post(
            self._url("peer_files"),
            {"file": SimpleUploadedFile(name, content)},
            HTTP_X_VAULT_API_KEY=self.raw_key)

    def test_upload_lands_owned_by_the_exporter(self):
        resp = self._post()
        self.assertEqual(resp.status_code, 201)
        row = json.loads(resp.content)
        vf = VaultFile.objects.get(bucket=self.bucket, key=row["key"])
        self.assertEqual(vf.owner, self.owner)
        self.assertFalse(vf.is_public)

    def test_colliding_names_get_suffixed_keys(self):
        first = json.loads(self._post().content)["key"]
        second = json.loads(self._post().content)["key"]
        self.assertNotEqual(first, second)

    def test_oversized_upload_is_a_413(self):
        with mock.patch("toto.vault.peer_views._storage_backends."
                        "EXTERNAL_UPLOAD_MAX_BYTES", 4):
            resp = self._post(content=b"more than four")
        self.assertEqual(resp.status_code, 413)

    def test_scan_refusal_is_a_400_with_the_verdict(self):
        refused = _scanning.Verdict.refused("active-content",
                                            "script element")
        with mock.patch("toto.vault.peer_views._scanning.should_scan",
                        return_value=True), \
             mock.patch("toto.vault.peer_views._scanning.scan",
                        return_value=refused):
            resp = self._post(name="evil.svg", content=b"<svg/>")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(json.loads(resp.content)["reason"], "active-content")
        self.assertFalse(
            VaultFile.objects.filter(bucket=self.bucket).exists())

    def test_missing_file_field_is_a_400(self):
        resp = self.client.post(self._url("peer_files"), {},
                                HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 400)


class MeteringTests(PeerApiTestCase):
    """The peer door runs the SAME ladder as the gateway and API doors.

    For a while it did not: scan-then-persist with no check_quota and no
    transfer_mb made cross-host inbound the free lane, capped by nothing
    until the nightly gb_day sweep noticed the bucket had grown. The billed
    subject is the exporter — the bucket's owner — matching both the levy
    and the door's own ownership rule.
    """

    def _post(self, name="a.txt", content=b"abc"):
        return self.client.post(
            self._url("peer_files"),
            {"file": SimpleUploadedFile(name, content)},
            HTTP_X_VAULT_API_KEY=self.raw_key)

    def test_a_landed_upload_writes_both_usage_events(self):
        from .models import VaultUsageEvent

        resp = self._post(content=b"x" * 2048)
        self.assertEqual(resp.status_code, 201)
        request_event = VaultUsageEvent.objects.get(
            metric_code="storage.request")
        transfer_event = VaultUsageEvent.objects.get(
            metric_code="storage.transfer_mb")
        # Billed to the exporter, not to some request.user (there is none —
        # the caller is a host), and sized from the actual bytes.
        self.assertEqual(request_event.user, self.owner)
        self.assertEqual(transfer_event.user, self.owner)
        self.assertEqual(transfer_event.quantity * (2 ** 20), 2048)

    def test_the_exporters_request_cap_refuses_before_any_write(self):
        from .models import VaultQuotaPolicy

        VaultQuotaPolicy.objects.create(metric_code="storage.request",
                                        limit=0)
        resp = self._post()
        self.assertEqual(resp.status_code, 429)
        self.assertIn("quota exceeded",
                      json.loads(resp.content)["error"])
        self.assertFalse(VaultFile.objects.filter(bucket=self.bucket).exists())

    def test_the_transfer_cap_counts_the_actual_megabytes(self):
        from .models import VaultQuotaPolicy

        VaultQuotaPolicy.objects.create(metric_code="storage.transfer_mb",
                                        limit="0.001")           # ~1 KiB
        self.assertEqual(self._post(content=b"x" * 4096).status_code, 429)
        self.assertEqual(self._post(content=b"x" * 16).status_code, 201)

    def test_a_frozen_exporter_answers_402_and_stores_nothing(self):
        # The arrears write-block reaches this door like every metered one:
        # an unpaid levy means no NEW bytes by any lane, deleting nothing.
        with mock.patch("toto.quota.levies.user_is_frozen",
                        return_value=True):
            resp = self._post()
        self.assertEqual(resp.status_code, 402)
        self.assertIn("went unpaid", json.loads(resp.content)["error"])
        self.assertFalse(VaultFile.objects.filter(bucket=self.bucket).exists())

    def test_a_peer_download_lands_on_the_exporters_egress_meter(self):
        # Bytes OUT through the peer door ride the same choke point as the
        # public download door; the exporter is the subject both ways.
        from .models import VaultUsageEvent

        self._file(content=b"y" * 1024)
        resp = self._get("peer_file_download", key="doc")
        self.assertEqual(resp.status_code, 200)
        b"".join(resp.streaming_content)
        event = VaultUsageEvent.objects.get(metric_code="storage.egress_mb")
        self.assertEqual(event.user, self.owner)
        self.assertEqual(event.quantity * (2 ** 20), 1024)

    def test_an_unpriced_host_meters_but_never_charges(self):
        # Doctrine: the cap half always runs, the charge half no-ops on a
        # None tariff. No Tariff row exists here, so the upload lands, the
        # events land, and no UsageRecord is minted anywhere.
        from django.apps import apps as django_apps

        from .models import VaultUsageEvent

        self.assertEqual(self._post().status_code, 201)
        self.assertEqual(VaultUsageEvent.objects.count(), 2)
        if django_apps.is_installed("toto.tariffs"):
            from toto.tariffs.models import UsageRecord

            self.assertEqual(UsageRecord.objects.count(), 0)


class DeleteTests(PeerApiTestCase):
    """A peer's DELETE moves the file to this host's trash (2026-10-01): it
    was a purge. The peer sees the file gone at once; its owner here can
    restore it until the nightly purge."""

    def _delete(self, key="doc"):
        return self.client.delete(self._url("peer_file_detail", key=key),
                                  HTTP_X_VAULT_API_KEY=self.raw_key)

    def test_delete_moves_the_file_to_the_trash_with_its_bytes(self):
        vf = self._file()
        stored_name = vf.file.name
        resp = self._delete()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content), {"ok": True, "deleted": "doc"})
        row = VaultFile.all_objects.get(pk=vf.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertIsNone(row.trashed_by)
        from .storage_backends import get_bucket_storage
        self.assertTrue(get_bucket_storage(self.bucket).exists(stored_name))

    def test_the_peer_sees_it_gone(self):
        self._file()
        self._delete()
        self.assertEqual(self._get("peer_file_detail", key="doc").status_code, 404)
        self.assertEqual(self._get("peer_file_download", key="doc").status_code, 404)
        self.assertEqual(json.loads(self._get("peer_files").content)["files"], [])
        self.assertEqual(json.loads(self._get("peer_manifest").content)["total_files"], 0)
        self.assertEqual(self._delete().status_code, 404)

    def test_recorded_once_as_trashed_naming_the_share(self):
        from toto.audit.models import AuditRecord

        BucketGrant.objects.filter(pk=self.grant.pk).update(label="placidia")
        vf = self._file(key="secret-name")
        self._delete(key="secret-name")
        # One act, one record: FileAuditMiddleware leaves the marked request.
        self.assertEqual(list(AuditRecord.objects.filter(app_label="vault")
                              .values_list("action", flat=True)), ["FILE_TRASHED"])
        entry = AuditRecord.objects.get(action="FILE_TRASHED")
        self.assertEqual((entry.object_id, entry.actor_user), (str(vf.pk), None))
        self.assertEqual((entry.metadata["door"], entry.metadata["share"],
                          entry.metadata["peer"]),
                         ("peer_delete", self.grant.pk, "placidia"))
        for secret in ("secret-name", self.raw_key, self.grant.magic_token):
            self.assertNotIn(secret, str(entry.metadata))

    def test_its_owner_here_restores_it_and_the_peer_sees_it_again(self):
        from .trash import restore_file, trashed_for

        vf = self._file()
        self._delete()
        trashed = VaultFile.all_objects.get(pk=vf.pk)
        self.assertEqual(list(trashed_for(self.owner)), [trashed])
        restore_file(trashed, by=self.owner)
        self.assertEqual(self._get("peer_file_detail", key="doc").status_code, 200)

    def test_the_peer_may_upload_the_same_name_again(self):
        self._file()
        self._delete()
        resp = self.client.post(self._url("peer_files"),
                                {"file": SimpleUploadedFile("doc.txt", b"new")},
                                HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(json.loads(resp.content)["key"], "doc")

    def test_meta_get_answers_one_row(self):
        self._file()
        data = json.loads(self._get("peer_file_detail", key="doc").content)
        self.assertEqual(data["key"], "doc")
