"""The trash's hiding (2026-10-01): a trashed file is gone from every read
door and still there for what must see it.

``VaultFile.objects`` hides the trash; ``VaultFile.all_objects`` does not.
These pin both halves: the listing, the JSON list API, the picker's search,
the peer API, the editor, the download door and the zip builder answer as if
the file did not exist — while the storage levy, the bucket figures and the
bucket purge still count or take it, its versions still find it, and its key
is free for a new upload.

    manage.py test toto.vault.tests_trash
"""

import os
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.core.models import Platform
from toto.vault.models import (
    Bucket,
    FileVersion,
    VaultDirectory,
    VaultFile,
    trash_days,
)
from toto.vault.peering import BucketGrant

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-trash-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="folder", bucket=cls.bucket,
                                                   owner=cls.owner)

    def file(self, key, body=b"hello", *, directory=None, public=False):
        vault_file = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                               file_type="text", bucket=self.bucket,
                               directory=directory, is_public=public)
        vault_file.file.save(f"{key}.txt", ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def setUp(self):
        self.live = self.file("live", b"live bytes", directory=self.folder)
        self.gone = self.file("gone", b"trashed bytes!", directory=self.folder)
        self.gone.trash(self.owner)


class ModelTests(_Fixture):
    def test_trash_stamps_and_leaves_the_folder(self):
        row = VaultFile.all_objects.get(pk=self.gone.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertEqual((row.trashed_by, row.trashed_from, row.directory),
                         (self.owner, self.folder, None))

    def test_trash_twice_keeps_the_first_stamp(self):
        first = VaultFile.all_objects.get(pk=self.gone.pk).trashed_at
        self.gone.trash(None)
        row = VaultFile.all_objects.get(pk=self.gone.pk)
        self.assertEqual((row.trashed_at, row.trashed_by), (first, self.owner))

    def test_the_default_manager_hides_and_all_objects_does_not(self):
        self.assertEqual(list(VaultFile.objects.values_list("pk", flat=True)), [self.live.pk])
        self.assertEqual(set(VaultFile.all_objects.values_list("pk", flat=True)),
                         {self.live.pk, self.gone.pk})
        self.assertEqual(list(self.bucket.files.values_list("pk", flat=True)), [self.live.pk])

    def test_a_version_still_finds_its_trashed_file(self):
        from toto.vault import versions

        live = self.file("versioned")
        version = versions.save_version(live, author=self.owner, label="v")
        live.trash(self.owner)
        version = FileVersion.objects.get(pk=version.pk)
        self.assertEqual(version.file.pk, live.pk)

    def test_the_key_is_free_for_a_new_upload(self):
        again = self.file("gone", b"new")
        self.assertEqual(VaultFile.objects.get(bucket=self.bucket, key="gone").pk, again.pk)

    def test_two_live_files_still_cannot_share_a_key(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            VaultFile.objects.create(owner=self.owner, title="x", key="live",
                                     file_type="text", bucket=self.bucket)

    def test_a_remote_bucket_s_file_cannot_be_trashed(self):
        mounted = Bucket.objects.create(name="Mounted", slug="mounted", owner=self.owner,
                                        storage_backend="remote_toto")
        stub = VaultFile(owner=self.owner, title="r", key="r", file_type="text",
                         bucket=mounted)
        self.assertFalse(stub.can_be_trashed)
        self.assertFalse(VaultFile(owner=self.owner, origin="mirror").can_be_trashed)
        self.assertTrue(self.live.can_be_trashed)

    @override_settings(VAULT_TRASH_DAYS=7)
    def test_trash_days_reads_the_setting(self):
        self.assertEqual(trash_days(), 7)

    def test_trash_days_defaults_to_thirty(self):
        self.assertEqual(trash_days(), 30)


class HiddenDoorTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_the_directory_listing(self):
        response = self.client.get(reverse("vault:public_list"))
        ids = {i["id"] for i in response.context["flat_items"] if i["t"] == "file"}
        self.assertIn(self.live.pk, ids)
        self.assertNotIn(self.gone.pk, ids)

    def test_the_json_list_api(self):
        keys = {f["key"] for f in self.client.get(reverse("vault:api_file_list")).json()["files"]}
        self.assertEqual(keys, {"live"})

    def test_the_json_detail_api(self):
        self.assertEqual(self.client.get(reverse("vault:api_file_detail", args=["gone"]))
                         .status_code, 404)

    def test_the_picker_search(self):
        from toto.vault.filetree import accessible_files, build_file_tree

        self.assertEqual(list(accessible_files(self.owner).values_list("pk", flat=True)),
                         [self.live.pk])
        titles = [row["title"] for b in build_file_tree(self.owner)
                  for g in b["groups"] for row in g["files"]]
        self.assertEqual(titles, ["live.txt"])

    def test_the_editor_open_door(self):
        self.assertEqual(self.client.get(reverse("editor:text_display", args=[self.gone.pk]))
                         .status_code, 404)

    def test_the_download_and_preview_door(self):
        self.assertEqual(self.client.get(reverse("vault:public_file", args=["owned", "gone"]))
                         .status_code, 404)
        self.assertEqual(self.client.get(reverse("vault:file_services", args=[self.gone.pk]))
                         .status_code, 404)

    def test_the_zip_builder(self):
        from toto.vault.archive import zip_files_to_vault_file

        with self.assertRaises(ValueError):
            zip_files_to_vault_file(self.owner, self.folder, None, [self.gone.pk], "x.zip")
        archive, added = zip_files_to_vault_file(
            self.owner, self.folder, None, [self.gone.pk, self.live.pk], "y.zip")
        self.assertEqual(added, 1)

    def test_the_other_file_doors_answer_404(self):
        for name, data in (("rename_file", {"file_pk": self.gone.pk, "title": "x.txt"}),
                           ("move_file", {"file_pk": self.gone.pk,
                                          "destination_directory": self.folder.pk}),
                           ("delete_file", {"file_pk": self.gone.pk})):
            self.assertEqual(self.client.post(reverse(f"vault:{name}"), data).status_code,
                             404, name)


class PeerDoorTests(_Fixture):
    def setUp(self):
        super().setUp()
        self.grant = BucketGrant.objects.create(label="peer", bucket=self.bucket,
                                                may_list=True, may_download=True)
        self.raw_key = self.grant.issue_api_key()
        self.grant.save()

    def get(self, name, **extra):
        return self.client.get(
            reverse(f"vault:{name}", kwargs={"grant_uid": self.grant.grant_uid,
                                             "magic_token": self.grant.magic_token, **extra}),
            HTTP_X_VAULT_API_KEY=self.raw_key)

    def test_the_manifest_counts_live_files(self):
        self.assertEqual(self.get("peer_manifest").json()["total_files"], 1)

    def test_the_listing(self):
        body = self.get("peer_files").json()
        self.assertEqual(([f["key"] for f in body["files"]], body["total"]), (["live"], 1))

    def test_the_detail_and_the_download(self):
        self.assertEqual(self.get("peer_file_detail", key="gone").status_code, 404)
        self.assertEqual(self.get("peer_file_download", key="gone").status_code, 404)


class StillCountedTests(_Fixture):
    def test_the_storage_levy_bills_trashed_bytes(self):
        from toto.vault.taxes import PlaintextLevy, StorageLevy

        both = len(b"live bytes") + len(b"trashed bytes!")
        self.assertEqual(StorageLevy().measure(self.owner), both)
        self.assertEqual(dict(StorageLevy().sample()), {self.owner.pk: both})
        self.assertEqual(PlaintextLevy().measure(self.owner), both)

    def test_the_bucket_totals_count_the_trash(self):
        from toto.vault.storage_adapters import file_totals

        self.assertEqual(file_totals(self.bucket),
                         (2, len(b"live bytes") + len(b"trashed bytes!")))

    def test_the_bucket_quota_figure_counts_the_trash(self):
        self.client.force_login(self.owner)
        info = self.client.get(reverse("vault:public_list")).context["bucket_quota_info"]
        used = round((len(b"live bytes") + len(b"trashed bytes!")) / 1_048_576, 2)
        self.assertEqual(info[self.bucket.pk]["used_mb"], used)

    def test_the_bucket_purge_takes_the_trash_too(self):
        from toto.vault import bucket_lifecycle

        path = VaultFile.all_objects.get(pk=self.gone.pk).file.path
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=timezone.now())
        result = bucket_lifecycle.purge_bucket(self.bucket.pk)
        self.assertTrue(result["ok"], result)
        self.assertFalse(VaultFile.all_objects.filter(pk=self.gone.pk).exists())
        self.assertFalse(Bucket.objects.filter(pk=self.bucket.pk).exists())
        self.assertFalse(os.path.exists(path))

    def test_deleting_the_folder_forgets_where_it_came_from(self):
        self.folder.delete()
        row = VaultFile.all_objects.get(pk=self.gone.pk)
        self.assertIsNone(row.trashed_from)
        self.assertIsNotNone(row.trashed_at)
