"""The nightly trash purge (2026-10-01): what has waited ``VAULT_TRASH_DAYS``
goes for good, nothing younger, and a second run is a no-op.

Pins ``trash.purge_expired`` (rows, bytes, the audit record, a failure left
for the next night), the beat entry that runs it, and that deleting a bucket
takes its trash whatever its age.

    manage.py test toto.vault.tests_trash_purge
"""

import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from toto.core.models import Platform
from toto.vault import trash
from toto.vault.models import Bucket, VaultFile

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-trash-purge-"), VAULT_TRASH_DAYS=30)
class PurgeExpiredTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.bucket = Bucket.objects.create(name="Owned", slug="owned", owner=cls.owner)

    def file(self, key, *, days_ago=None, bucket=None):
        vault_file = VaultFile(owner=self.owner, title=f"{key}.txt", key=key,
                               file_type="text", bucket=bucket or self.bucket)
        vault_file.file.save(f"{key}.txt", ContentFile(b"bytes of " + key.encode()), save=False)
        vault_file.save()
        if days_ago is not None:
            vault_file.trash(self.owner)
            VaultFile.all_objects.filter(pk=vault_file.pk).update(
                trashed_at=timezone.now() - timedelta(days=days_ago))
        return VaultFile.all_objects.get(pk=vault_file.pk)

    def setUp(self):
        self.live = self.file("live")
        self.young = self.file("young", days_ago=29)
        self.old = self.file("old", days_ago=31)
        self.older = self.file("older", days_ago=400)

    def pks(self):
        return set(VaultFile.all_objects.values_list("pk", flat=True))

    def test_only_the_expired_trash_goes(self):
        paths = [self.old.file.path, self.older.file.path]
        with self.captureOnCommitCallbacks(execute=True):
            result = trash.purge_expired()
        self.assertEqual((result["purged"], result["failed"], result["more"]), (2, 0, False))
        self.assertEqual(self.pks(), {self.live.pk, self.young.pk})
        for path in paths:
            self.assertFalse(os.path.exists(path), path)
        self.assertTrue(os.path.exists(self.young.file.path))
        self.assertTrue(os.path.exists(self.live.file.path))

    def test_a_second_run_is_a_no_op(self):
        with self.captureOnCommitCallbacks(execute=True):
            trash.purge_expired()
        before = self.pks()
        with self.captureOnCommitCallbacks(execute=True):
            result = trash.purge_expired()
        self.assertEqual((result["purged"], result["failed"]), (0, 0))
        self.assertEqual(self.pks(), before)

    @override_settings(VAULT_TRASH_DAYS=7)
    def test_the_setting_moves_the_cutoff(self):
        trash.purge_expired()
        self.assertEqual(self.pks(), {self.live.pk})

    def test_a_bucket_being_deleted_is_left_to_its_own_purge(self):
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=timezone.now())
        result = trash.purge_expired()
        self.assertEqual(result["purged"], 0)
        self.assertIn(self.old.pk, self.pks())

    def test_a_failed_file_stays_trashed_for_the_next_night(self):
        from toto.vault.storage_backends import LocalVaultStorageDriver

        real = LocalVaultStorageDriver.delete_strict
        old_name = self.old.file.name

        def refuse(driver, name):
            if name == old_name:
                raise OSError("read-only disk")
            return real(driver, name)

        with mock.patch.object(LocalVaultStorageDriver, "delete_strict", refuse), \
                self.assertLogs("toto.vault", level="WARNING") as logs:
            result = trash.purge_expired()
        self.assertEqual((result["purged"], result["failed"], result["ok"]), (1, 1, False))
        row = VaultFile.all_objects.get(pk=self.old.pk)
        self.assertIsNotNone(row.trashed_at)
        self.assertTrue(os.path.exists(row.file.path))
        self.assertTrue(any("read-only disk" in line and str(self.old.pk) in line
                            for line in logs.output), logs.output)
        # The next night, with the disk fixed, it goes.
        result = trash.purge_expired()
        self.assertEqual(result["purged"], 1)
        self.assertNotIn(self.old.pk, self.pks())

    def test_the_budget_leaves_the_rest_for_the_next_night(self):
        result = trash.purge_expired(budget=0)
        self.assertEqual((result["purged"], result["more"]), (0, True))
        self.assertIn(self.old.pk, self.pks())

    def test_small_batches_still_take_everything(self):
        result = trash.purge_expired(batch=1)
        self.assertEqual(result["purged"], 2)
        self.assertEqual(self.pks(), {self.live.pk, self.young.pk})

    def test_each_purged_file_is_on_the_audit_chain(self):
        from django.apps import apps

        if not apps.is_installed("toto.audit"):
            self.skipTest("toto.audit is not installed")
        from toto.audit.models import AuditRecord

        trash.purge_expired()
        events = AuditRecord.objects.filter(action=trash.FILE_PURGED, app_label="vault")
        self.assertEqual({e.object_id for e in events}, {str(self.old.pk), str(self.older.pk)})
        for event in events:
            self.assertIsNone(event.actor_user_id)
            self.assertEqual(event.metadata.get("door"), "trash_expired")
            self.assertNotIn("old.txt", str(event.metadata))

    def test_the_task_runs_the_purge(self):
        from toto.vault.tasks import purge_expired_trash

        result = purge_expired_trash()
        self.assertEqual(result["purged"], 2)

    def test_deleting_the_bucket_takes_its_trash_of_any_age(self):
        from toto.vault import bucket_lifecycle

        paths = [self.young.file.path, self.old.file.path]
        Bucket.objects.filter(pk=self.bucket.pk).update(deletion_requested_at=timezone.now())
        result = bucket_lifecycle.purge_bucket(self.bucket.pk)
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.pks(), set())
        self.assertFalse(Bucket.objects.filter(pk=self.bucket.pk).exists())
        for path in paths:
            self.assertFalse(os.path.exists(path), path)


class BeatEntryTests(SimpleTestCase):
    def test_the_nightly_purge_is_scheduled_when_enabled(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(vault_trash=True)["vault-trash-purge"]
        self.assertEqual(entry["task"], "toto.vault.tasks.purge_expired_trash")
        self.assertNotIn("vault-trash-purge", beat_schedule())

    def test_the_host_schedules_it_by_default(self):
        from django.conf import settings

        # A host that does not declare the switch (or turned it off) is
        # not this test's business.
        if getattr(settings, "VAULT_TRASH_PURGE", False) is not True:
            self.skipTest("the host does not switch the nightly purge on")
        schedule = settings.CELERY_BEAT_SCHEDULE
        self.assertEqual(schedule["vault-trash-purge"]["task"],
                         "toto.vault.tasks.purge_expired_trash")
