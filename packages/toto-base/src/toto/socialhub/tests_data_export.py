"""*Download my data* (2026-10-01, RODO; on the own profile's Your data tab
since 2026-10-02, stage 50): the button queues a
job, the job files the zip in the member's personal bucket, the page shows
how it went and links it.

One open export per member, one a day (a failed one does not count), none
without a worker; a stale open row is closed so it never blocks for good.
The export is the member's own, whatever the form carries, and an earlier
export is not packed into the next.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub import data_export
from toto.socialhub.models import DataExport
from toto.socialhub.tasks import build_data_export

User = get_user_model()


class _Queued:
    id = "task-1"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="data-export-"))
class DataExportTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.ada = User.objects.create_user("ada", "ada@example.test", "Correct-horse-9")
        self.bob = User.objects.create_user("bob", "bob@example.test", "Correct-horse-8")
        person = Person.objects.create(user=self.ada, display_name="Ada")
        self.data_tab = reverse("socialhub:profile_details", args=[person.slug]) + "?tab=data"
        self.client.force_login(self.ada)
        worker = mock.patch.object(data_export, "worker_available", return_value=True)
        worker.start()
        self.addCleanup(worker.stop)
        self.delay = mock.patch.object(build_data_export, "delay", return_value=_Queued())
        self.queued = self.delay.start()
        self.addCleanup(self.delay.stop)

    def press(self, **data):
        return self.client.post(reverse("account:data_export"), data)

    def page(self):
        return self.client.get(reverse("account:home") + "?tab=data", follow=True)

    def messages(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class RequestTests(DataExportTestCase):
    def test_the_button_queues_one_export_of_their_own(self):
        response = self.press(user=self.bob.pk)
        self.assertRedirects(response, self.data_tab + "#data", fetch_redirect_response=False)
        export = DataExport.objects.get()
        self.assertEqual((export.user, export.status, export.task_id),
                         (self.ada, DataExport.PENDING, "task-1"))
        self.queued.assert_called_once_with(export.pk)
        rec = AuditRecord.objects.get(action="PRIVACY.EXPORT_REQUESTED")
        self.assertEqual(rec.actor_user, self.ada)

    def test_a_get_queues_nothing(self):
        self.assertEqual(self.client.get(reverse("account:data_export")).status_code, 405)
        self.assertFalse(DataExport.objects.exists())

    def test_signed_out_queues_nothing(self):
        self.client.logout()
        self.press()
        self.assertFalse(DataExport.objects.exists())

    def test_a_second_press_while_one_is_open_is_refused(self):
        self.press()
        response = self.press()
        self.assertEqual(DataExport.objects.count(), 1)
        self.assertIn("already being prepared", " ".join(self.messages(response)))

    def test_the_database_refuses_two_open_exports(self):
        DataExport.objects.create(user=self.ada)
        with self.assertRaises(IntegrityError), transaction.atomic():
            DataExport.objects.create(user=self.ada, status=DataExport.RUNNING)

    def test_once_a_day(self):
        DataExport.objects.create(user=self.ada, status=DataExport.READY,
                                  created_at=timezone.now() - timedelta(hours=23))
        response = self.press()
        self.assertEqual(DataExport.objects.count(), 1)
        self.assertIn("once a day", " ".join(self.messages(response)))
        DataExport.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.press()
        self.assertEqual(DataExport.objects.count(), 2)

    def test_a_failed_export_does_not_count(self):
        DataExport.objects.create(user=self.ada, status=DataExport.FAILED)
        self.press()
        self.assertEqual(DataExport.objects.filter(status=DataExport.PENDING).count(), 1)

    def test_another_members_export_does_not_count(self):
        DataExport.objects.create(user=self.bob)
        self.press()
        self.assertEqual(DataExport.objects.filter(user=self.ada).count(), 1)

    def test_no_worker_no_row(self):
        with mock.patch.object(data_export, "worker_available", return_value=False):
            response = self.press()
        self.assertFalse(DataExport.objects.exists())
        self.assertIn("background worker", " ".join(self.messages(response)))
        self.queued.assert_not_called()

    def test_a_stale_open_export_is_closed_and_no_longer_blocks(self):
        stale = DataExport.objects.create(user=self.ada,
                                          created_at=timezone.now() - timedelta(hours=7))
        self.press()
        stale.refresh_from_db()
        self.assertEqual(stale.status, DataExport.FAILED)
        self.assertTrue(AuditRecord.objects.filter(action="PRIVACY.EXPORT_FAILED").exists())
        self.assertEqual(DataExport.objects.filter(status=DataExport.PENDING).count(), 1)


class BuildTests(DataExportTestCase):
    def build(self):
        export = DataExport.objects.create(user=self.ada)
        build_data_export(export.pk)
        export.refresh_from_db()
        return export

    def test_the_zip_lands_in_their_personal_bucket(self):
        export = self.build()
        self.assertEqual(export.status, DataExport.READY)
        f = export.output
        self.assertEqual((f.owner, f.bucket.slug, f.file_type, f.is_public),
                         (self.ada, "personal-ada", "zip", False))
        with f.file.open("rb") as fh, zipfile.ZipFile(io.BytesIO(fh.read())) as zf:
            self.assertEqual(json.loads(zf.read("account.json"))[0]["username"], "ada")
        self.assertEqual(export.summary["tables"]["account"], 1)
        rec = AuditRecord.objects.get(action="PRIVACY.EXPORT_READY")
        self.assertIsNone(rec.actor_user)
        self.assertEqual(rec.metadata["vault_file"], f.pk)

    def test_the_member_s_point_on_the_map_is_in_the_copy(self):
        """Geography (2026-10-06): where the point is, its name and its note;
        nobody else's point, and no community's headquarters."""
        from django.apps import apps

        if not apps.is_installed("toto.geography"):
            self.skipTest("no geography on this host")
        from django.contrib.gis.geos import Point

        from toto.geography.models import Address, PersonAddress

        ada = Person.objects.get(user=self.ada)
        bob = Person.objects.create(user=self.bob, display_name="Bob")
        PersonAddress.objects.create(person=ada, address=Address.objects.create(
            point=Point(21.0122, 52.2297, srid=4326), name="Home", note="third floor"))
        PersonAddress.objects.create(person=bob, address=Address.objects.create(
            point=Point(18.6466, 54.352, srid=4326), name="Bob's"))
        export = self.build()
        self.assertEqual(export.status, DataExport.READY)
        with export.output.file.open("rb") as fh, zipfile.ZipFile(io.BytesIO(fh.read())) as zf:
            rows = json.loads(zf.read("geography_address.json"))
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["latitude"], rows[0]["longitude"], rows[0]["name"],
                          rows[0]["note"]), (52.2297, 21.0122, "Home", "third floor"))
        self.assertEqual(export.summary["tables"]["geography_address"], 1)

    def test_a_redelivered_job_does_nothing_twice(self):
        export = self.build()
        build_data_export(export.pk)
        self.assertEqual(DataExport.objects.get().output_id, export.output_id)
        self.assertEqual(AuditRecord.objects.filter(action="PRIVACY.EXPORT_READY").count(), 1)

    def test_an_earlier_export_is_not_packed_into_the_next(self):
        first = self.build()
        # Read now: the next copy purges this one once it is ready (2026-10-01,
        # tests_export_replaced) — but it is still there while that is built.
        first_title = first.output.title
        DataExport.objects.update(created_at=timezone.now() - timedelta(days=2))
        second = self.build()
        with second.output.file.open("rb") as fh, zipfile.ZipFile(io.BytesIO(fh.read())) as zf:
            titles = [r["title"] for r in json.loads(zf.read("files/index.json"))]
        self.assertNotIn(first_title, titles)

    def test_a_failure_closes_the_row_with_a_sentence(self):
        with mock.patch("toto.core.personal_data.write_zip", side_effect=RuntimeError("disk")):
            export = self.build()
        self.assertEqual(export.status, DataExport.FAILED)
        self.assertNotIn("disk", export.error)
        self.assertIsNone(export.output)


class PageTests(DataExportTestCase):
    def test_the_section_offers_the_button(self):
        response = self.page()
        self.assertContains(response, reverse("account:data_export"))
        self.assertContains(response, "Download my data")

    def test_a_ready_export_is_linked_and_the_button_waits(self):
        export = DataExport.objects.create(user=self.ada)
        build_data_export(export.pk)
        export.refresh_from_db()
        response = self.page()
        self.assertContains(response, export.output.get_public_url())
        self.assertNotContains(response, f'action="{reverse("account:data_export")}"')
        self.assertContains(response, "You can ask for a new copy after")

    def test_another_members_export_is_not_shown(self):
        export = DataExport.objects.create(user=self.bob)
        build_data_export(export.pk)
        export.refresh_from_db()
        response = self.page()
        self.assertNotContains(response, export.output.get_public_url())
        self.assertContains(response, f'action="{reverse("account:data_export")}"')
