"""purge_file — the driver-aware permanent delete, and who is allowed to call it.

``purge_file`` still exists and still deletes: a person deleting their own file
means it. What is gone is the caller that used it WITHOUT a person — the storage
levy's ``enforce()``, which shed randomly chosen files a week after a missed
payment. Falling behind now stops new uploads and takes nothing away, so the
tests for that walk are gone with the code, and the last class here asserts the
method cannot come back by accident.
"""

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


class NoEnforcementTests(TestCase):
    """The levy provider cannot shed anything, and there is no way to ask it to.

    This is a guard rather than a behaviour test: the contract lost ``enforce``
    (see toto/quota/levy.py) because nothing on this platform should delete a
    person's files to settle a bill. If either of these fails, that decision is
    being quietly reversed.
    """

    def test_the_provider_has_no_enforce(self):
        self.assertFalse(hasattr(StorageLevy(), "enforce"))

    def test_the_contract_has_no_enforce(self):
        from toto.quota.levy import LevyProvider

        self.assertFalse(hasattr(LevyProvider, "enforce"))

    def test_the_provider_says_what_falling_behind_costs(self):
        # And says it in its own words, so the arrears notice never tells a
        # storage debtor that their time dials will be reset, or the reverse.
        self.assertIn("nothing you have stored is deleted",
                      StorageLevy.consequence_text)
