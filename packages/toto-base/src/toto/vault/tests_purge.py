"""purge_file (driver-aware permanent delete) and the storage levy's enforce."""

import os
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import ProtectedError
from django.test import TestCase

from toto.vault.models import Bucket, VaultFile
from toto.vault.purge import purge_file
from toto.vault.taxes import StorageLevy

User = get_user_model()


class FakeRng:
    """Deterministic: 'shuffles' into ascending (pk, size) order."""

    def shuffle(self, rows):
        rows.sort()


def make_file(owner, size, bucket=None):
    name = f"purge-{owner.pk}-{size}-{VaultFile.objects.count()}.txt"
    return VaultFile.objects.create(
        owner=owner, title=name, file_type="text",
        file=SimpleUploadedFile(name, b"x"),
        file_size_bytes=size, bucket=bucket,
    )


class PurgeFileTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")

    def test_local_bucketless_file_row_and_bytes_gone(self):
        vf = make_file(self.user, 10)
        path = vf.file.path
        self.assertTrue(os.path.exists(path))

        with self.captureOnCommitCallbacks(execute=True):
            purge_file(vf)

        self.assertFalse(VaultFile.objects.filter(pk=vf.pk).exists())
        self.assertFalse(os.path.exists(path))

    def test_remote_bucket_calls_the_driver_after_commit(self):
        bucket = Bucket.objects.create(name="s3b", slug="s3b", owner=self.user,
                                       storage_backend="s3")
        vf = make_file(self.user, 10, bucket=bucket)
        stored_name = vf.file.name
        driver = MagicMock()

        with patch("toto.vault.purge.get_bucket_storage",
                   return_value=driver) as factory:
            with self.captureOnCommitCallbacks(execute=True):
                purge_file(vf)

        factory.assert_called_once_with(bucket)
        driver.delete.assert_called_once_with(stored_name)
        self.assertFalse(VaultFile.objects.filter(pk=vf.pk).exists())

    def test_protected_error_propagates_before_anything_physical(self):
        vf = make_file(self.user, 10)
        path = vf.file.path
        driver = MagicMock()

        with patch("toto.vault.purge.get_bucket_storage", return_value=driver), \
             patch.object(VaultFile, "delete",
                          side_effect=ProtectedError("pinned", set())):
            with self.assertRaises(ProtectedError):
                with self.captureOnCommitCallbacks(execute=True):
                    purge_file(vf)

        driver.delete.assert_not_called()
        self.assertTrue(VaultFile.objects.filter(pk=vf.pk).exists())
        self.assertTrue(os.path.exists(path))


class EnforceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.provider = StorageLevy()

    def test_stops_at_the_target_and_no_further(self):
        for _ in range(5):
            make_file(self.user, 10)

        result = self.provider.enforce(self.user, 25, rng=FakeRng())

        self.assertEqual(result.deleted_count, 3)
        self.assertEqual(result.deleted_raw, 30)
        self.assertEqual(result.final_raw, 20)
        self.assertTrue(result.reached_target)
        self.assertEqual(VaultFile.objects.filter(owner=self.user).count(), 2)

    def test_zero_target_deletes_everything(self):
        for _ in range(3):
            make_file(self.user, 10)

        result = self.provider.enforce(self.user, 0, rng=FakeRng())

        self.assertEqual(result.deleted_count, 3)
        self.assertEqual(result.final_raw, 0)
        self.assertTrue(result.reached_target)

    def test_under_target_deletes_nothing(self):
        make_file(self.user, 10)

        result = self.provider.enforce(self.user, 25, rng=FakeRng())

        self.assertEqual(result.deleted_count, 0)
        self.assertTrue(result.reached_target)
        self.assertEqual(VaultFile.objects.count(), 1)

    def test_only_the_targets_files_are_touched(self):
        bob = User.objects.create_user("bob", password="pw")
        make_file(bob, 100)
        make_file(self.user, 10)

        self.provider.enforce(self.user, 0, rng=FakeRng())

        self.assertEqual(VaultFile.objects.filter(owner=bob).count(), 1)

    def test_protected_file_is_skipped_and_another_drawn(self):
        files = [make_file(self.user, 10) for _ in range(5)]
        protected_pk = files[0].pk  # first in FakeRng order
        real_purge = purge_file
        deleted, skipped = [], []

        def guarded(vf):
            if vf.pk == protected_pk:
                raise ProtectedError("pinned", set())
            real_purge(vf)

        with patch("toto.vault.purge.purge_file", side_effect=guarded):
            result = self.provider.enforce(
                self.user, 25, rng=FakeRng(),
                on_deleted=lambda info: deleted.append(info["pk"]),
                on_skipped=lambda info: skipped.append(info["pk"]),
            )

        self.assertEqual(skipped, [protected_pk])
        self.assertEqual(result.skipped_count, 1)
        # The protected 10 bytes stay, so three others go: 50 → 20 ≤ 25.
        self.assertEqual(result.deleted_count, 3)
        self.assertEqual(result.final_raw, 20)
        self.assertTrue(result.reached_target)
        self.assertTrue(VaultFile.objects.filter(pk=protected_pk).exists())

    def test_all_protected_reports_not_reached(self):
        for _ in range(3):
            make_file(self.user, 10)

        with patch("toto.vault.purge.purge_file",
                   side_effect=ProtectedError("pinned", set())):
            result = self.provider.enforce(self.user, 5, rng=FakeRng())

        self.assertEqual(result.deleted_count, 0)
        self.assertEqual(result.skipped_count, 3)
        self.assertFalse(result.reached_target)
        self.assertEqual(result.final_raw, 30)
