"""Tests for the vault hardening contract flags.

``VAULT_EXTERNAL_BUCKETS = False`` / ``VAULT_FILE_EDITS = False`` are host
contract flags (the faros onion host sets both); the library defaults keep
every behavior unchanged. This module deliberately avoids any editor-app
import so a host without ``toto.editor`` can run it via
``manage.py test toto.vault.tests_hardening``. Permissive-side tests set
their flag explicitly, so the module passes on hosts that turn either off.
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

    @override_settings(VAULT_EXTERNAL_BUCKETS=True)
    def test_flag_on_allows(self):
        self.assertTrue(external_buckets_allowed())

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_remote_refresh_is_refused(self):
        # The refresh door replaced the old import-remote door; the flag
        # still closes it first, before any ownership or backend check.
        resp = self._client().post(
            reverse("vault:bucket_refresh", args=["hardening-local"]))
        self.assertEqual(resp.status_code, 403)
        self.assertIn("disabled", resp.json()["error"])

    @override_settings(VAULT_EXTERNAL_BUCKETS=True)
    def test_remote_refresh_open_when_allowed(self):
        # A LOCAL bucket past the flag gate answers 400 ("not a remote
        # bucket"), which proves the flag itself no longer blocks.
        resp = self._client().post(
            reverse("vault:bucket_refresh", args=["hardening-local"]))
        self.assertEqual(resp.status_code, 400)

    @override_settings(VAULT_EXTERNAL_BUCKETS=False)
    def test_driver_chokepoint_refuses_non_local(self):
        with self.assertRaisesMessage(RuntimeError, "disabled"):
            get_bucket_storage(self.s3)
        self.assertIsInstance(get_bucket_storage(self.local), LocalVaultStorageDriver)

    @override_settings(VAULT_EXTERNAL_BUCKETS=True)
    def test_driver_chokepoint_open_when_allowed(self):
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

    @override_settings(VAULT_EXTERNAL_BUCKETS=True)
    def test_public_base_url_used_when_allowed(self):
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

    @override_settings(VAULT_FILE_EDITS=True)
    def test_content_put_works_when_allowed(self):
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


_SCRATCH_MEDIA = tempfile.mkdtemp(prefix="vault-hardening-")


@override_settings(MEDIA_ROOT=_SCRATCH_MEDIA)
class RefusedFileTypesTests(TestCase):
    """``VAULT_REFUSED_FILE_TYPES`` — a type ban enforced at the doors.

    Detection stays honest (the refusal can then name the type) and rows
    that predate the ban keep working; what the flag closes is every door
    that ASSIGNS a type: the three uploads, rename, empty-file creation.
    The peer door carries the same three lines as the API door tested
    here; its fixtures (grants, peers) live in ``tests_peer_api``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("refuser", "r@x.com", "pw")

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _upload(self, name, content=b"x", expect=201):
        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client.post(
            reverse("vault:api_file_upload"),
            {"file": SimpleUploadedFile(name, content,
                                        content_type="text/plain")},
        )
        self.assertEqual(resp.status_code, expect, resp.content)
        return resp

    def test_the_default_refuses_nothing(self):
        from toto.vault.models import refused_file_types
        self.assertEqual(refused_file_types(), frozenset())
        self._upload("paper.tex", expect=201)

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_an_api_upload_of_a_refused_type_is_400(self):
        from toto.vault.models import VaultFile
        resp = self._upload("paper.tex", expect=400)
        self.assertIn("latex", resp.json()["error"])
        self.assertFalse(VaultFile.objects.exists())

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_every_latex_extension_is_covered(self):
        """The ban is by TYPE: .tex, .sty and the rest of the family."""
        for name in ("a.tex", "a.sty", "a.cls", "a.dtx", "a.ins"):
            with self.subTest(name=name):
                self._upload(name, expect=400)

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_a_rename_cannot_smuggle_the_type_in(self):
        from toto.vault.models import VaultFile
        self._upload("note.txt")
        vault_file = VaultFile.objects.get()
        resp = self.client.post(reverse("vault:rename_file"), {
            "file_pk": vault_file.pk, "title": "paper.tex",
            "file_type": "latex"})
        self.assertEqual(resp.status_code, 400)
        vault_file.refresh_from_db()
        self.assertEqual(vault_file.file_type, "text")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_the_create_menu_stops_offering_it(self):
        from toto.vault.views import available_create_types
        self.assertNotIn("latex",
                         {t for t, _ in available_create_types()})

    @override_settings(VAULT_REFUSED_FILE_TYPES={"latex"})
    def test_the_gateway_refuses_and_keeps_the_batch(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from toto.vault.models import (Bucket, FileGateway, VaultDirectory,
                                       VaultFile)
        bucket = Bucket.objects.create(name="Gate", owner=self.user,
                                       slug="hardening-gate")
        directory = VaultDirectory.objects.create(
            bucket=bucket, name="inbox", owner=self.user)
        FileGateway.objects.create(directory=directory, bucket=bucket,
                                   name="gate")
        resp = self.client.post(
            reverse("vault:gateway_upload", args=[directory.pk]),
            {"file": SimpleUploadedFile("paper.tex", b"\\documentclass")})
        self.assertFalse(VaultFile.objects.filter(file_type="latex").exists())
        self.assertIn(b"refused", resp.content)
