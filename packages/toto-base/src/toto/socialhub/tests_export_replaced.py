"""Only the latest *Download my data* zip is kept (2026-10-01, the review of
stage 35; ``data_export.replace_earlier``, ``vault.trash.purge_now``).

Every zip stayed in the member's bucket, outside any quota, one a day for
ever. Now a new copy, once ready, deletes each earlier one for good — the
vault's purge, not the trash, so an old copy of personal data does not wait
there for a month — live or trashed, wherever it was moved, and marks its row
REPLACED. A copy whose bytes will not delete keeps its row ready for the next
export to try again; nobody else's copy is touched.

    manage.py test toto.socialhub.tests_export_replaced
"""

from __future__ import annotations

import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import data_export
from toto.socialhub.models import DataExport
from toto.socialhub.tasks import build_data_export

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="export-replaced-"))
class ReplacedCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.ada = User.objects.create_user("ada", "ada@example.test", "Correct-horse-9")
        self.bob = User.objects.create_user("bob", "bob@example.test", "Correct-horse-8")
        Person.objects.create(user=self.ada, display_name="Ada")

    def build(self, user=None):
        export = DataExport.objects.create(user=user or self.ada)
        build_data_export(export.pk)
        export.refresh_from_db()
        self.assertEqual(export.status, DataExport.READY)
        return export

    def file_of(self, export):
        """The zip's row id and where its bytes are on disk."""
        return export.output_id, export.output.file.path

    def assertPurged(self, file_id, path):
        from toto.vault.models import VaultFile

        self.assertFalse(VaultFile.all_objects.filter(pk=file_id).exists())
        self.assertFalse(os.path.exists(path))

    def assertReplaced(self, export):
        export.refresh_from_db()
        self.assertEqual((export.status, export.output_id), (DataExport.REPLACED, None))


class ReplaceTests(ReplacedCase):
    def test_a_new_copy_purges_the_one_before_and_marks_its_row(self):
        first = self.build()
        file_id, path = self.file_of(first)
        second = self.build()
        self.assertReplaced(first)
        self.assertPurged(file_id, path)
        self.assertTrue(os.path.exists(second.output.file.path))
        self.assertEqual(data_export.latest_for(self.ada), second)
        purged = AuditRecord.objects.get(action="FILE_PURGED")
        self.assertEqual((purged.object_id, purged.actor_user_id), (str(file_id), None))
        self.assertEqual((purged.metadata["door"], purged.metadata["export"]),
                         ("data_export_replaced", first.pk))

    def test_a_copy_in_the_trash_is_purged_not_left_there(self):
        from toto.vault import trash

        first = self.build()
        file_id, path = self.file_of(first)
        trash.remove_file(first.output, by=self.ada, door="test")
        self.build()
        self.assertReplaced(first)
        self.assertPurged(file_id, path)
        self.assertFalse(trash.trashed_for(self.ada).exists())

    def test_a_copy_moved_to_another_bucket_is_purged_too(self):
        from toto.vault.models import Bucket, VaultFile

        first = self.build()
        file_id, path = self.file_of(first)
        archive = Bucket.objects.create(name="archive", slug="archive-ada", owner=self.ada)
        VaultFile.objects.filter(pk=file_id).update(bucket=archive, title="kept.zip")
        self.build()
        self.assertReplaced(first)
        self.assertPurged(file_id, path)

    def test_a_row_whose_file_is_already_gone_is_marked(self):
        from toto.vault.purge import purge_file

        first = self.build()
        purge_file(first.output)
        self.build()
        self.assertReplaced(first)

    def test_nobody_elses_copy_and_no_failed_row_is_touched(self):
        bobs = self.build(self.bob)
        failed = DataExport.objects.create(user=self.ada, status=DataExport.FAILED,
                                           error="The export failed.")
        self.build()
        bobs.refresh_from_db()
        self.assertEqual(bobs.status, DataExport.READY)
        self.assertTrue(os.path.exists(bobs.output.file.path))
        failed.refresh_from_db()
        self.assertEqual(failed.status, DataExport.FAILED)

    def test_a_copy_that_will_not_purge_keeps_its_row_for_the_next_copy(self):
        first = self.build()
        file_id, path = self.file_of(first)
        # The strict purge raises when the bytes will not delete (a refused
        # S3 key, a read-only disk) and keeps the row: vault's tests_purge.
        with mock.patch("toto.vault.purge.purge_file", side_effect=OSError("read-only disk")):
            second = self.build()
        first.refresh_from_db()
        self.assertEqual((first.status, first.output_id), (DataExport.READY, file_id))
        self.assertTrue(os.path.exists(path))
        self.assertEqual(second.status, DataExport.READY)
        self.assertFalse(AuditRecord.objects.filter(action="FILE_PURGED").exists())
        # The next export (here, its replacement step) takes it.
        self.assertEqual(data_export.replace_earlier(second), 1)
        self.assertReplaced(first)
        self.assertPurged(file_id, path)


class PageTests(ReplacedCase):
    def test_the_section_says_a_new_copy_replaces_the_one_before(self):
        self.client.force_login(self.ada)
        response = self.client.get(reverse("account:home") + "?tab=data", follow=True)
        self.assertContains(response, "A new copy replaces the one before: the earlier zip "
                                      "is deleted for good, not moved to the trash.")
        self.assertContains(response, "It also holds what the audit trail records others "
                                      "doing about you, without their address and browser.")
