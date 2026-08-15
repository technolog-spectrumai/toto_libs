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
            "HTTP_X_VAULT_API_KEY": extra.pop("api_key", self.raw_key)})

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


class DeleteTests(PeerApiTestCase):
    def test_delete_purges_row_and_bytes(self):
        vf = self._file()
        stored_name = vf.file.name
        resp = self.client.delete(self._url("peer_file_detail", key="doc"),
                                  HTTP_X_VAULT_API_KEY=self.raw_key)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(
            VaultFile.objects.filter(bucket=self.bucket, key="doc").exists())
        from .storage_backends import get_bucket_storage
        self.assertFalse(get_bucket_storage(self.bucket).exists(stored_name))

    def test_meta_get_answers_one_row(self):
        self._file()
        data = json.loads(self._get("peer_file_detail", key="doc").content)
        self.assertEqual(data["key"], "doc")
