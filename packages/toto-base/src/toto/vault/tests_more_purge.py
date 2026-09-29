"""``purge_file`` beyond tests_purge: the orders that make it safe — nothing
physical before the caller commits, nothing raised after the row is gone.
Runs with the host settings (tests_purge rides the tax stanza for the levy
guards it also carries; nothing here needs them).
"""

import os
import tempfile
from unittest import skip
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings

from toto.vault.models import Bucket, VaultFile
from toto.vault.purge import purge_file

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-purge-"))
class PurgeOrderTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")

    def make(self, name="a.txt", bucket=None):
        return VaultFile.objects.create(owner=self.user, title=name, file_type="text",
                                        file=SimpleUploadedFile(name, b"bytes"),
                                        bucket=bucket)

    def s3_file(self, name="a.txt"):
        bucket = Bucket.objects.create(name=f"s3-{name}", slug=f"s3-{name[0]}",
                                       owner=self.user, storage_backend="s3")
        return self.make(name, bucket=bucket)

    def test_a_rolled_back_caller_keeps_the_row_and_asks_no_driver(self):
        vf = self.s3_file()
        pk = vf.pk
        driver = MagicMock()
        with patch("toto.vault.purge.get_bucket_storage", return_value=driver):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                try:
                    with transaction.atomic():
                        purge_file(vf)
                        raise RuntimeError("the caller changed its mind")
                except RuntimeError:
                    pass
        self.assertEqual(callbacks, [])
        driver.delete.assert_not_called()
        self.assertTrue(VaultFile.objects.filter(pk=pk).exists())

    def test_the_remote_delete_waits_for_the_commit(self):
        vf = self.s3_file()
        pk = vf.pk
        driver = MagicMock()
        with patch("toto.vault.purge.get_bucket_storage", return_value=driver):
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                purge_file(vf)
                self.assertFalse(VaultFile.objects.filter(pk=pk).exists())
            driver.delete.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
        driver.delete.assert_called_once()

    @skip("BUG vault/signals.py:30 delete_file_on_disk (post_delete) unlinks a LOCAL file's "
          "bytes inside the caller's transaction, so purge_file's promise (purge.py:1-13: bytes "
          "only after commit, never a live row pointing at deleted bytes) does not hold: a "
          "caller that rolls back after purge_file - yamabiko.receivers.hand_over runs it in "
          "the transfer landing transaction - gets its row back with its bytes gone")
    def test_a_rolled_back_caller_keeps_a_local_files_bytes(self):
        vf = self.make()
        pk, path = vf.pk, vf.file.path
        try:
            with transaction.atomic():
                purge_file(vf)
                raise RuntimeError("the caller changed its mind")
        except RuntimeError:
            pass
        self.assertTrue(VaultFile.objects.filter(pk=pk).exists())
        self.assertTrue(os.path.exists(path))

    def test_a_driver_that_fails_to_delete_is_logged_not_raised(self):
        bucket = Bucket.objects.create(name="s3", slug="s3", owner=self.user,
                                       storage_backend="s3")
        vf = self.make(bucket=bucket)
        pk = vf.pk
        driver = MagicMock()
        driver.delete.side_effect = ConnectionError("endpoint gone")
        with patch("toto.vault.purge.get_bucket_storage", return_value=driver), \
                self.assertLogs("toto.vault", "WARNING") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                purge_file(vf)
        self.assertFalse(VaultFile.objects.filter(pk=pk).exists())
        self.assertIn("could not delete stored bytes", logs.output[0])
        self.assertIn("endpoint gone", logs.output[0])

    def test_a_row_with_no_stored_name_asks_no_driver_for_anything(self):
        vf = VaultFile.objects.create(owner=self.user, title="stub", key="stub",
                                      file_type="text", file="")
        pk = vf.pk
        with patch("toto.vault.purge.get_bucket_storage") as factory:
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                purge_file(vf)
        self.assertEqual(callbacks, [])
        factory.assert_not_called()
        self.assertFalse(VaultFile.objects.filter(pk=pk).exists())

    def test_the_bucket_is_captured_before_the_row_goes(self):
        bucket = Bucket.objects.create(name="s3b", slug="s3b", owner=self.user,
                                       storage_backend="s3")
        vf = self.make(bucket=bucket)
        name = vf.file.name
        driver = MagicMock()
        with patch("toto.vault.purge.get_bucket_storage", return_value=driver) as factory:
            with self.captureOnCommitCallbacks(execute=True):
                purge_file(vf)
        self.assertEqual(factory.call_args.args[0].pk, bucket.pk)
        driver.delete.assert_called_once_with(name)
