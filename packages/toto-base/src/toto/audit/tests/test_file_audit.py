"""Every vault file operation lands in the chain — success and refusal.

The vault is vendored code, so the wiring is a middleware watching the
resolved namespace, not calls sprinkled through suite views. These tests
drive the REAL vault endpoints and read the chain back.
"""

from __future__ import annotations

import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from django.contrib.auth import get_user_model

_SCRATCH_MEDIA = tempfile.mkdtemp(prefix="placidia-file-audit-")


def actions():
    return list(AuditRecord.objects.filter(app_label="vault")
                .values_list("action", flat=True))


@override_settings(MEDIA_ROOT=_SCRATCH_MEDIA)
class FileAuditTests(TestCase):
    def setUp(self):
        from toto.vault.models import Bucket

        self.user = get_user_model().objects.create_user(
            "filer", password="pw", is_staff=True)
        self.client.force_login(self.user)
        self.bucket = Bucket.objects.create(name="Trail", slug="trail",
                                            owner=self.user)

    def _upload(self, name="note.txt", body=b"hello"):
        from toto.vault.models import FileGateway, VaultDirectory

        directory = VaultDirectory.objects.create(
            bucket=self.bucket, name="inbox", owner=self.user)
        FileGateway.objects.create(directory=directory, bucket=self.bucket,
                                   name="inbox gate")
        response = self.client.post(
            reverse("vault:gateway_upload", args=[directory.pk]),
            {"file": SimpleUploadedFile(name, body)})
        return response

    def test_an_upload_is_recorded(self):
        response = self._upload()
        self.assertLess(response.status_code, 400)
        self.assertIn("FILE_UPLOADED", actions())

    def test_a_delete_is_recorded(self):
        from toto.vault.models import VaultFile

        self._upload()
        vault_file = VaultFile.objects.get()
        self.client.post(reverse("vault:delete_file"),
                         {"file_pk": vault_file.pk})
        # A delete moves the file to the trash (2026-10-01), recorded as
        # such — and once, not also as the url's FILE_DELETED.
        self.assertIn("FILE_TRASHED", actions())
        self.assertNotIn("FILE_DELETED", actions())

    def test_a_rename_is_recorded_with_the_object(self):
        from toto.vault.models import VaultFile

        self._upload()
        vault_file = VaultFile.objects.get()
        self.client.post(reverse("vault:rename_file"),
                         {"file_pk": vault_file.pk, "title": "renamed.txt",
                          "file_type": vault_file.file_type})
        row = AuditRecord.objects.filter(action="FILE_RENAMED").first()
        self.assertIsNotNone(row)
        # These endpoints carry the file in the BODY, not the url — the
        # middleware reads file_pk from the body so the trail still names
        # the file.
        self.assertTrue(row.success)
        self.assertEqual(row.object_id, str(vault_file.pk))
        self.assertEqual(row.metadata.get("url_name"), "rename_file")

    def test_a_download_is_recorded(self):
        from toto.vault.models import VaultFile

        self._upload()
        vault_file = VaultFile.objects.get()
        VaultFile.objects.filter(pk=vault_file.pk).update(is_public=True)
        self.client.get(reverse("vault:public_file",
                                args=[self.bucket.slug, vault_file.key]))
        self.assertIn("FILE_DOWNLOADED", actions())

    def test_listings_do_not_flood_the_chain(self):
        # The vault's pages render through PageProcessor, which 404s without
        # an active Platform. Seeded directly rather than through a host's
        # own init command: this app ships in the suite now.
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            active=True,
            defaults={"site_name": "Test", "author": "Tests",
                      "publication_year": 2026})
        before = AuditRecord.objects.count()
        self.client.get(reverse("vault:public_list"))
        self.client.get(reverse("vault:api_file_list"))
        self.assertEqual(AuditRecord.objects.count(), before)

    def test_a_refused_operation_is_recorded_as_failed(self):
        from toto.vault.models import VaultFile

        self._upload()
        vault_file = VaultFile.objects.get()
        self.client.logout()
        self.client.force_login(get_user_model().objects.create_user("stranger", password="pw"))
        response = self.client.post(reverse("vault:delete_file"),
                                    {"file_pk": vault_file.pk})
        self.assertGreaterEqual(response.status_code, 400)
        row = AuditRecord.objects.filter(action="FILE_DELETED").latest("id")
        self.assertFalse(row.success)

    def test_an_unmapped_vault_mutation_still_lands(self):
        """The generic net: new suite doors are audited before anyone maps
        them."""
        from toto.audit.middleware import VAULT_ACTIONS, VAULT_IGNORED

        self.client.post(reverse("vault:api_strongbox"), {})
        row = AuditRecord.objects.filter(app_label="vault").latest("id")
        self.assertEqual(row.action, "VAULT_ACTION")
        self.assertEqual(row.metadata.get("url_name"), "api_strongbox")

    def test_the_chain_still_verifies_after_file_traffic(self):
        from toto.audit.services import verify_chain

        self._upload()
        report = verify_chain()
        self.assertTrue(report["ok"] if isinstance(report, dict) else report)
