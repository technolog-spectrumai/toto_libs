"""The delete doors and the trash (2026-10-01): every door a member deletes
through moves the file to the trash, recorded as FILE_TRASHED — distinct
from a real FILE_DELETED — and a mounted remote bucket's file still goes at
once. The delete signal never unlinks a trashed row's bytes while its
transaction can roll back, and the figures that read the reverse join count
live files while their sizes keep the trash.

    manage.py test toto.vault.tests_trash_doors
"""

import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import transaction
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.vault.models import Bucket, VaultDirectory, VaultFile
from toto.vault.trash import AUDITED_ATTR, remove_file

User = get_user_model()


def vault_actions():
    return list(AuditRecord.objects.filter(app_label="vault").order_by("sequence")
                .values_list("action", flat=True))


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-trash-doors-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="folder", bucket=cls.bucket,
                                                   owner=cls.owner)

    def file(self, key, body=b"hello", *, bucket=None, file_type="text", ext="txt"):
        vault_file = VaultFile(owner=self.owner, title=f"{key}.{ext}", key=key,
                               file_type=file_type, bucket=bucket or self.bucket,
                               directory=self.folder)
        vault_file.file.save(f"{key}.{ext}", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def mounted(self):
        return Bucket.objects.create(name="Mounted", slug="mounted", owner=self.owner,
                                     storage_backend="remote_toto")


class RemoveFileTests(_Fixture):
    def test_a_file_goes_to_the_trash_with_its_bytes(self):
        vault_file = self.file("doc")
        path = vault_file.file.path
        self.assertTrue(remove_file(vault_file, by=self.owner, door="t"))
        row = VaultFile.all_objects.get(pk=vault_file.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertEqual(row.trashed_by, self.owner)
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(vault_actions(), ["FILE_TRASHED"])

    def test_the_record_names_the_file_and_no_title(self):
        vault_file = self.file("secret-name")
        remove_file(vault_file, by=self.owner, door="memo_delete")
        entry = AuditRecord.objects.get(action="FILE_TRASHED")
        self.assertEqual((entry.object_id, entry.object_type, entry.actor_user),
                         (str(vault_file.pk), "VaultFile", self.owner))
        self.assertEqual(entry.metadata["door"], "memo_delete")
        self.assertNotIn("secret-name", str(entry.metadata))

    def test_a_remote_bucket_s_file_is_deleted_at_once(self):
        vault_file = VaultFile.objects.create(owner=self.owner, title="r.txt", key="r",
                                              file_type="text", bucket=self.mounted())
        self.assertFalse(remove_file(vault_file, by=self.owner, door="t"))
        self.assertFalse(VaultFile.all_objects.filter(pk=vault_file.pk).exists())
        entry = AuditRecord.objects.get(app_label="vault")
        self.assertEqual((entry.action, entry.object_id), ("FILE_DELETED", str(vault_file.pk)))

    def test_the_request_is_marked_so_the_middleware_stays_out(self):
        request = RequestFactory().post("/x/")
        request.user = self.owner
        remove_file(self.file("doc"), by=self.owner, request=request, door="t")
        self.assertTrue(getattr(request, AUDITED_ATTR))


class SignalTests(_Fixture):
    def test_trashing_never_unlinks(self):
        vault_file = self.file("doc")
        vault_file.trash(self.owner)
        self.assertTrue(os.path.isfile(vault_file.file.path))

    def test_a_rolled_back_delete_of_a_trashed_row_keeps_its_bytes(self):
        vault_file = self.file("doc")
        vault_file.trash(self.owner)
        path = vault_file.file.path

        class Undo(Exception):
            pass

        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    VaultFile.all_objects.get(pk=vault_file.pk).delete()
                    raise Undo
            except Undo:
                pass
        self.assertTrue(os.path.isfile(path))
        self.assertTrue(VaultFile.all_objects.filter(pk=vault_file.pk).exists())

    def test_a_committed_delete_of_a_trashed_row_leaves_no_orphan(self):
        vault_file = self.file("doc")
        vault_file.trash(self.owner)
        path = vault_file.file.path
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            VaultFile.all_objects.get(pk=vault_file.pk).delete()
        self.assertTrue(os.path.isfile(path))        # not before the commit
        for callback in callbacks:
            callback()
        self.assertFalse(os.path.isfile(path))

    def test_a_live_row_s_delete_still_unlinks_at_once(self):
        vault_file = self.file("doc")
        path = vault_file.file.path
        vault_file.delete()
        self.assertFalse(os.path.isfile(path))


class DoorTests(_Fixture):
    def setUp(self):
        self.client.force_login(self.owner)

    def assert_trashed(self, vault_file):
        self.assertFalse(VaultFile.objects.filter(pk=vault_file.pk).exists())
        self.assertIsNotNone(VaultFile.all_objects.get(pk=vault_file.pk).trashed_at)
        self.assertTrue(os.path.isfile(vault_file.file.path))
        self.assertIn("FILE_TRASHED", vault_actions())
        self.assertNotIn("FILE_DELETED", vault_actions())

    def test_the_vault_listing_delete(self):
        vault_file = self.file("doc")
        response = self.client.post(reverse("vault:delete_file"), {"file_pk": vault_file.pk})
        self.assertEqual(response.json(), {"ok": True, "trashed": True})
        self.assert_trashed(vault_file)
        self.assertEqual(vault_actions().count("FILE_TRASHED"), 1)

    def test_the_vault_listing_delete_in_a_mounted_bucket_is_a_delete(self):
        vault_file = VaultFile.objects.create(owner=self.owner, title="r.txt", key="r",
                                              file_type="text", bucket=self.mounted())
        response = self.client.post(reverse("vault:delete_file"), {"file_pk": vault_file.pk})
        self.assertEqual(response.json(), {"ok": True, "trashed": False})
        self.assertFalse(VaultFile.all_objects.filter(pk=vault_file.pk).exists())
        self.assertEqual(vault_actions(), ["FILE_DELETED"])

    def test_the_json_api_delete(self):
        vault_file = self.file("doc")
        response = self.client.delete(reverse("vault:api_file_detail", args=["doc"]))
        self.assertEqual(response.status_code, 204)
        self.assert_trashed(vault_file)
        self.assertEqual(vault_actions(), ["FILE_TRASHED"])

    def test_the_editor_delete(self):
        vault_file = self.file("doc")
        response = self.client.post(reverse("editor:text_delete", args=[vault_file.pk]))
        self.assertEqual(response.status_code, 200)
        self.assert_trashed(vault_file)

    def test_a_refused_delete_is_still_recorded_as_a_failed_delete(self):
        stranger = User.objects.create_user("stranger", password="pw")
        vault_file = self.file("doc")
        self.client.force_login(stranger)
        response = self.client.post(reverse("vault:delete_file"), {"file_pk": vault_file.pk})
        self.assertEqual(response.status_code, 404)
        row = AuditRecord.objects.get(app_label="vault")
        self.assertEqual((row.action, row.success), ("FILE_DELETED", False))


class FigureTests(_Fixture):
    def setUp(self):
        self.client.force_login(self.owner)
        self.live = self.file("live", b"12345")
        self.gone = self.file("gone", b"1234567890")
        self.gone.trash(self.owner)

    def test_the_metrics_api_counts_live_and_sizes_stored(self):
        data = self.client.get(reverse("vault:api_metrics")).json()
        self.assertEqual(data["total_files"], 1)
        self.assertEqual(data["total_size_bytes"], 15)
        self.assertEqual(data["trash_size_bytes"], 10)
        bucket = {b["slug"]: b for b in data["bucket_stats"]}["owned"]
        self.assertEqual((bucket["file_count"], bucket["total_size"], bucket["trash_size"]),
                         (1, 15, 10))

    def test_a_bucket_holding_only_trash_is_not_one_i_have_files_in(self):
        other = User.objects.create_user("other", password="pw")
        theirs = Bucket.objects.create(name="Theirs", slug="theirs", owner=other)
        mine = self.file("mine", bucket=theirs)
        slugs = {b["slug"] for b in self.client.get(reverse("vault:api_bucket_tree"))
                 .json()["buckets"]}
        self.assertIn("theirs", slugs)
        mine.trash(self.owner)
        slugs = {b["slug"] for b in self.client.get(reverse("vault:api_bucket_tree"))
                 .json()["buckets"]}
        self.assertNotIn("theirs", slugs)


class OtherAppDoorTests(_Fixture):
    """The delete doors of the apps that keep their documents in the vault:
    each moves the file to the trash, recorded as FILE_TRASHED. Called as
    views, not through urls: a host mounts only some of them (zenobia mounts
    memo's reader urls only, and no notebook routes)."""

    def post(self, view, *args):
        request = RequestFactory().post("/x/")
        request.user = self.owner
        request.session = {}
        request._dont_enforce_csrf_checks = True
        return view(request, *args), request

    def assert_trashed(self, vault_file):
        row = VaultFile.all_objects.get(pk=vault_file.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertEqual(row.trashed_by, self.owner)
        self.assertTrue(os.path.isfile(vault_file.file.path))
        self.assertEqual(vault_actions(), ["FILE_TRASHED"])

    def test_a_deck(self):
        from django.apps import apps

        if not apps.is_installed("toto.memo"):
            self.skipTest("toto.memo is not installed on this host")
        from toto.memo.views import presentation_delete

        deck = self.file("deck", b"<presentation/>", file_type="pxml", ext="pxml")
        response, request = self.post(presentation_delete, deck.pk)
        self.assertEqual(response.status_code, 302)
        self.assert_trashed(deck)
        self.assertTrue(getattr(request, AUDITED_ATTR))

    def test_a_notebook(self):
        from unittest import mock

        from django.apps import apps

        if not apps.is_installed("toto.mandragora"):
            self.skipTest("toto.mandragora is not installed on this host")
        from toto.mandragora import tpy_format, tpy_views

        body = tpy_format.dumps(tpy_format.new_notebook("N")).encode()
        notebook = self.file("nb", body, file_type="xml", ext="xml")
        with mock.patch.object(tpy_views, "client"):
            response, _request = self.post(tpy_views.tpy_delete, notebook.pk)
        self.assertEqual(response.status_code, 200)
        self.assert_trashed(notebook)

    def test_a_drawing(self):
        from django.apps import apps

        if not apps.is_installed("toto.sketch"):
            self.skipTest("toto.sketch is not installed on this host")
        from toto.sketch.views import sketch_delete

        drawing = self.file("drawing", b"<svg xmlns='http://www.w3.org/2000/svg'/>",
                            file_type="svg", ext="svg")
        response, _request = self.post(sketch_delete, drawing.pk)
        self.assertEqual(response.status_code, 302)
        self.assert_trashed(drawing)

    def test_a_workspace_file(self):
        from django.apps import apps

        if not apps.is_installed("toto.ambrosia"):
            self.skipTest("toto.ambrosia is not installed on this host")
        from toto.ambrosia import services

        source = self.file("main", b"print(1)\n", file_type="text", ext="py")
        services.delete_file(workspace=None, vault_file=source, by=self.owner)
        self.assert_trashed(source)
