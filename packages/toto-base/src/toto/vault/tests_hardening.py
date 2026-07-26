"""Tests for the vault hardening contract flags.

``VAULT_EXTERNAL_BUCKETS = False`` / ``VAULT_FILE_EDITS = False`` are host
contract flags (the faros onion host sets both); the library defaults keep
every behavior unchanged. This module deliberately avoids any editor-app
import so a host without ``toto.editor`` can run it via
``manage.py test toto.vault.tests_hardening``.
"""
import tempfile

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.vault.models import Bucket, external_buckets_allowed
from toto.vault.storage_backends import (
    LocalVaultStorageDriver,
    S3CompatibleVaultStorageDriver,
    get_bucket_storage,
)


class ExternalBucketFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("hardening", "h@x.com", "pw")
        cls.local = Bucket.objects.create(
            name="Local", owner=cls.user, slug="hardening-local",
        )
        cls.s3 = Bucket.objects.create(
            name="S3", owner=cls.user, slug="hardening-s3",
            storage_backend="s3", storage_config={"bucket_name": "b"},
        )

    def _client(self):
        c = Client()
        c.force_login(self.user)
        return c

    def test_flag_defaults_on(self):
        self.assertTrue(external_buckets_allowed())

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_import_remote_is_refused(self):
        resp = self._client().post(
            reverse("vault:bucket_import_remote"),
            '{"url": "toto://other.example/vault/buckets/x/"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)
        self.assertIn("disabled", resp.json()["error"])

    def test_import_remote_still_works_by_default(self):
        resp = self._client().post(
            reverse("vault:bucket_import_remote"),
            '{"url": "toto://other.example/vault/buckets/x/"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 201)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_driver_chokepoint_refuses_non_local(self):
        with self.assertRaisesMessage(RuntimeError, "disabled"):
            get_bucket_storage(self.s3)
        self.assertIsInstance(get_bucket_storage(self.local), LocalVaultStorageDriver)

    def test_driver_chokepoint_open_by_default(self):
        self.assertIsInstance(get_bucket_storage(self.s3), S3CompatibleVaultStorageDriver)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_bucket_clean_rejects_non_local(self):
        bucket = Bucket(
            name="New S3", owner=self.user, slug="hardening-new-s3",
            storage_backend="s3",
        )
        with self.assertRaises(ValidationError):
            bucket.full_clean()
        local = Bucket(name="New Local", owner=self.user, slug="hardening-new-local")
        local.full_clean()  # must not raise

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_public_base_url_is_ignored(self):
        self.local.public_base_url = "https://cdn.example.com/vault/"
        self.assertEqual(self.local.get_public_file_url("k"), "")

    def test_public_base_url_used_by_default(self):
        self.local.public_base_url = "https://cdn.example.com/vault/"
        self.assertEqual(
            self.local.get_public_file_url("k"), "https://cdn.example.com/vault/k",
        )

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_connection_url_hidden_for_external_buckets(self):
        c = self._client()
        resp = c.get(reverse("vault:bucket_connection_url", args=[self.s3.slug]))
        self.assertEqual(resp.status_code, 404)
        resp = c.get(reverse("vault:bucket_connection_url", args=[self.local.slug]))
        self.assertEqual(resp.status_code, 200)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-hardening-"))
class FileEditsFlagTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("editflag", "e@x.com", "pw")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _upload(self, name="note.txt", content=b"hello"):
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile(name, content, content_type="text/plain")},
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        return resp.json()

    @override_settings(VAULT_FILE_EDITS=False)
    def test_uploads_still_work_but_report_not_editable(self):
        payload = self._upload()
        self.assertFalse(payload["is_editable"])

    @override_settings(VAULT_FILE_EDITS=False)
    def test_content_put_refused(self):
        key = self._upload()["key"]
        resp = self.client.put(
            reverse("vault:api_file_content", args=[key]),
            '{"content": "changed"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_content_put_works_by_default(self):
        key = self._upload()["key"]
        resp = self.client.put(
            reverse("vault:api_file_content", args=[key]),
            '{"content": "changed"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["is_editable"])

    @override_settings(VAULT_FILE_EDITS=False)
    def test_api_create_refused(self):
        resp = self.client.post(
            reverse("vault:api_file_create"),
            '{"bucket_slug": "x", "title": "a", "file_type": "text"}',
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    @override_settings(VAULT_FILE_EDITS=False)
    def test_create_types_menu_empties(self):
        from toto.vault.views import available_create_types
        self.assertEqual(available_create_types(), [])

    @override_settings(VAULT_FILE_EDITS=False)
    def test_create_empty_file_view_refused(self):
        resp = self.client.post(
            reverse("vault:create_file"),
            {"title": "a", "file_type": "text", "directory_id": "1"},
        )
        self.assertEqual(resp.status_code, 403)
