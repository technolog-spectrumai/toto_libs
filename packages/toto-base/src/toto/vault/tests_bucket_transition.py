"""A bucket's backend is immutable once it holds files.

The hole this closes moved money in both directions: StorageLevy._billable
excludes only remote_toto and attribution is VaultFile.owner, so a flip either
stopped billing bytes still on this disk, or started billing owners for stubs
whose bytes live on another host.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from toto.vault.models import Bucket, StorageBackend, VaultFile
from toto.vault.peering import BucketPeer

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(), VAULT_EXTERNAL_BUCKETS=True)
class BackendFreezeTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("freeze-op", password="x")
        self.bucket = Bucket.objects.create(
            name="Freeze", slug="freeze", owner=self.user,
            storage_backend=StorageBackend.LOCAL)

    def _add_file(self):
        return VaultFile.objects.create(
            owner=self.user, bucket=self.bucket, title="a.txt", key="a-txt",
            file_type="text")

    def test_an_empty_bucket_may_still_change_backend(self):
        fresh = Bucket.objects.get(pk=self.bucket.pk)
        fresh.storage_backend = StorageBackend.S3
        fresh.full_clean(exclude=["slug"])          # must not raise

    def test_a_backend_flip_is_refused_once_the_bucket_holds_files(self):
        self._add_file()
        fresh = Bucket.objects.get(pk=self.bucket.pk)
        fresh.storage_backend = StorageBackend.S3
        with self.assertRaises(ValidationError) as caught:
            fresh.full_clean(exclude=["slug"])
        self.assertIn("holds 1 file", str(caught.exception))

    def test_flipping_a_mounted_bucket_to_s3_is_refused_by_name(self):
        """The retroactive-billing case: taxes excludes only remote_toto."""
        peer = BucketPeer.objects.create(
            label="Peer", base_url="https://peer.example.org",
            grant_uid="00000000-0000-0000-0000-0000000000ab",
            magic_token="tok", remote_bucket_slug="theirs")
        self.bucket.storage_backend = StorageBackend.REMOTE_TOTO
        self.bucket.peer = peer
        self.bucket.save()
        self._add_file()

        fresh = Bucket.objects.get(pk=self.bucket.pk)
        fresh.storage_backend = StorageBackend.S3
        with self.assertRaises(ValidationError) as caught:
            fresh.full_clean(exclude=["slug"])
        message = str(caught.exception)
        self.assertIn("remote_toto", message)
        self.assertIn("who pays", message)

    def test_saving_without_changing_the_backend_is_untouched(self):
        self._add_file()
        fresh = Bucket.objects.get(pk=self.bucket.pk)
        fresh.name = "Renamed"
        fresh.full_clean(exclude=["slug"])          # must not raise
        fresh.save()
        self.assertEqual(Bucket.objects.get(pk=self.bucket.pk).name, "Renamed")

    def test_a_queryset_update_bypasses_the_check_and_that_is_documented(self):
        """clean() is a form-level guard, not a database constraint.

        Asserted out loud so nobody mistakes it for one: a data migration must
        still be able to move a bucket, and .update() is how it does that.
        """
        self._add_file()
        Bucket.objects.filter(pk=self.bucket.pk).update(
            storage_backend=StorageBackend.S3)
        self.assertEqual(Bucket.objects.get(pk=self.bucket.pk).storage_backend,
                         StorageBackend.S3)
