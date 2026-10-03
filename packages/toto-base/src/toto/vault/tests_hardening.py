"""Tests for the vault hardening contract flags.

``VAULT_EXTERNAL_BUCKETS = False`` / ``VAULT_FILE_EDITS = False`` are host
contract flags (the faros onion host sets both); the library defaults keep
every behavior unchanged. This module deliberately avoids any editor-app
import so a host without ``toto.editor`` can run it via
``manage.py test toto.vault.tests_hardening``. Permissive-side tests set
their flag explicitly, so the module passes on hosts that turn either off.

``VAULT_STORAGE_ONLY = True`` is the third (zenobia sets it, 2026-10-03) and
the same rule holds for it: a test that needs the New-file door sets the flag
off itself, wherever the test lives (``StorageOnlyFlagTests`` below).
"""
import tempfile

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.vault.models import (Bucket, VaultDirectory, VaultFile,
                               external_buckets_allowed, storage_only)
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

    # VAULT_STORAGE_ONLY off as well: the door has to be there to refuse.
    @override_settings(VAULT_FILE_EDITS=False, VAULT_STORAGE_ONLY=False)
    def test_create_empty_file_view_refused(self):
        resp = self.client.post(
            reverse("vault:create_file"),
            {"title": "a", "file_type": "text", "directory_id": "1"},
        )
        self.assertEqual(resp.status_code, 403)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-hardening-"))
class StorageOnlyFlagTests(TestCase):
    """``VAULT_STORAGE_ONLY`` — a vault that stores files and makes none.

    The New-file door (``vault:create_file``) is there only while the flag is
    off, and the flag is read on every request. So a test that asks something
    OF the door sets the flag off itself: the edits flag above, the Office
    sentence (``tests_office_refusal``) and the cross-site refusal
    (``toto.api.tests.test_origin_and_fetch_metadata``). Until 2026-10-03
    they relied on the library default and failed on the first host that set
    the flag. These say what such a host gets instead: 404 whatever is asked,
    and nothing written. The API's half (``api/files/create/`` without
    content) is ``tests_api.FileCreateWithContentApiTests``.
    """

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("storer", "s@x.com", "pw")
        cls.bucket = Bucket.objects.create(
            name="Store", owner=cls.user, slug="hardening-store")
        cls.directory = VaultDirectory.objects.create(
            bucket=cls.bucket, name="inbox", owner=cls.user)

    def setUp(self):
        self.client = Client()
        self.client.force_login(self.user)

    def _new_file(self, **fields):
        data = {"title": "a.txt", "file_type": "text",
                "directory_id": self.directory.pk}
        data.update(fields)
        return self.client.post(reverse("vault:create_file"), data)

    def test_the_library_default_is_off(self):
        from django.conf import settings

        with self.settings(VAULT_STORAGE_ONLY=True):
            self.assertTrue(storage_only())
            del settings.VAULT_STORAGE_ONLY     # a host that never named it
            self.assertFalse(storage_only())

    @override_settings(VAULT_STORAGE_ONLY=True)
    def test_the_new_file_door_is_gone_and_writes_nothing(self):
        asked = (
            {},
            {"title": "a.md", "file_type": "markdown"},
            {"title": "a.svg", "file_type": "svg"},
            {"title": "minutes.docx"},      # not the Office sentence either
            {"title": ""},
            {"file_type": "no-such-type"},
            {"directory_id": ""},
        )
        for fields in asked:
            with self.subTest(**fields):
                self.assertEqual(self._new_file(**fields).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("vault:create_file")).status_code, 404)
        self.assertFalse(VaultFile.objects.exists())

    @override_settings(VAULT_STORAGE_ONLY=True, VAULT_FILE_EDITS=False)
    def test_gone_whatever_the_edits_flag_says(self):
        # Both flags set: 404, not the edits flag's 403 — there is no door to
        # be refused at.
        self.assertEqual(self._new_file().status_code, 404)
        self.assertFalse(VaultFile.objects.exists())

    @override_settings(VAULT_STORAGE_ONLY=True)
    def test_the_create_menu_empties(self):
        from toto.vault.views import available_create_types
        self.assertEqual(available_create_types(), [])

    @override_settings(VAULT_STORAGE_ONLY=True)
    def test_an_upload_and_a_new_folder_are_still_taken(self):
        """What the flag leaves: a file arrives by upload, and a folder is
        not a file."""
        import json

        from django.core.files.uploadedfile import SimpleUploadedFile
        resp = self.client.post(reverse("vault:api_file_upload"), {
            "file": SimpleUploadedFile("note.txt", b"hello",
                                       content_type="text/plain"),
            "bucket_slug": self.bucket.slug,
            "directory_id": self.directory.pk})
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertEqual(VaultFile.objects.get().directory_id, self.directory.pk)
        resp = self.client.post(
            reverse("vault:api_directory_create"),
            json.dumps({"name": "scans", "parent_id": self.directory.pk,
                        "bucket_slug": self.bucket.slug}),
            content_type="application/json")
        self.assertEqual(resp.status_code, 201, resp.content)
        self.assertTrue(VaultDirectory.objects.filter(
            name="scans", parent=self.directory).exists())

    @override_settings(VAULT_STORAGE_ONLY=False, VAULT_FILE_EDITS=True)
    def test_the_door_answers_for_itself_when_the_flag_is_off(self):
        # Past both flags the door reads the request: no filename is its own
        # 400, which proves it is there (whether a file could then be made
        # depends on an editor being installed, which this module never asks).
        resp = self._new_file(title="")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", resp.json())
        self.assertFalse(VaultFile.objects.exists())

    def test_every_test_module_that_asks_an_empty_file_door_names_the_flag(self):
        """The rule in this class's docstring, kept by reading the sources: a
        test module of an installed toto app that goes to either empty-file
        door says which kind of host it is asking about. A module that names
        a door and never the flag is asking the host's setting by accident."""
        from pathlib import Path

        from django.apps import apps

        doors = ("vault:create_file", "/vault/file/create/",
                 "vault:api_file_create", "/vault/api/files/create/")
        asking, silent = 0, []
        for config in apps.get_app_configs():
            if not config.name.startswith("toto."):
                continue
            for path in Path(config.path).rglob("test*.py"):
                source = path.read_text(encoding="utf-8", errors="replace")
                if any(door in source for door in doors):
                    asking += 1
                    if "VAULT_STORAGE_ONLY" not in source:
                        silent.append(str(path))
        self.assertGreater(asking, 1, "the walk found this module alone: it is vacuous")
        self.assertEqual(silent, [])


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
        self.assertIn(b"does not accept latex files", resp.content)
