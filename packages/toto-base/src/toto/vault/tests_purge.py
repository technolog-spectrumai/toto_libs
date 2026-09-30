import tempfile
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
from django.test import TestCase, override_settings

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



# MEDIA_ROOT, not the host's: the real media dir is a root-owned docker bind
# mount, so every file-creating test here died on PermissionError before it
# asserted anything. The classes that already scoped their own temp dir
# (CopyFilesToBucketTest and friends in tests.py) are why the idiom exists;
# these ones never got it.
_MEDIA = tempfile.mkdtemp(prefix="vault-test-")

@override_settings(MEDIA_ROOT=_MEDIA)
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

    def test_version_bodies_only_this_file_cited_go_with_it(self):
        """A file's versions cascade with it; their bodies (VersionBlob) have
        no link back to the file and used to stay on disk for good."""
        from django.core.files.storage import default_storage

        from toto.vault import versions
        from toto.vault.models import VersionBlob

        vf = make_file(self.user, 10)
        keeper = make_file(self.user, 11)
        versions.save_version(vf, body=b"only mine", author=self.user)
        versions.save_version(vf, body=b"shared body", author=self.user)
        versions.save_version(keeper, body=b"shared body", author=self.user)
        mine = VersionBlob.objects.get(content_hash=versions._digest(b"only mine"))
        shared = VersionBlob.objects.get(content_hash=versions._digest(b"shared body"))
        mine_name = mine.data.name
        self.assertTrue(default_storage.exists(mine_name))

        with self.captureOnCommitCallbacks(execute=True):
            purge_file(vf)

        self.assertFalse(VersionBlob.objects.filter(pk=mine.pk).exists())
        self.assertFalse(default_storage.exists(mine_name))
        self.assertTrue(VersionBlob.objects.filter(pk=shared.pk).exists())
        self.assertTrue(default_storage.exists(shared.data.name))

    def test_nothing_of_a_pinned_file_goes(self):
        from toto.vault import versions
        from toto.vault.models import VersionBlob

        vf = make_file(self.user, 10)
        versions.save_version(vf, body=b"pinned body", author=self.user)
        with patch.object(VaultFile, "delete", side_effect=ProtectedError("pinned", set())):
            with self.assertRaises(ProtectedError):
                with self.captureOnCommitCallbacks(execute=True):
                    purge_file(vf, strict=True)
        self.assertTrue(VersionBlob.objects.filter(
            content_hash=versions._digest(b"pinned body")).exists())

    def test_strict_bytes_that_will_not_go_keep_the_row(self):
        vf = make_file(self.user, 10)
        pk = vf.pk
        driver = MagicMock()
        driver.delete_strict.side_effect = RuntimeError("AccessDenied")
        with self.assertRaises(RuntimeError):
            with self.captureOnCommitCallbacks(execute=True):
                purge_file(vf, driver=driver, strict=True)
        self.assertTrue(VaultFile.objects.filter(pk=pk).exists())
        driver.delete.assert_not_called()


@override_settings(MEDIA_ROOT=_MEDIA)
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
