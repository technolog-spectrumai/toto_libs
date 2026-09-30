"""The writer's doors follow the bucket's clearances (2026-09-30).

`_open_document` (the writer and its save), `_get_owned_file` (the source view)
and `_owned_file` (a rendition) used to fetch by the owner or a bridge alone, so
an owner who lacks their bucket's clearance still opened and saved there —
against "no owner bypass". They fetch through `access.gate_by_bucket` first now:
a file hidden by its bucket is 404, to its owner and to a lending bridge too.
"""

import json
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from toto.cyprian import htmldoc as df
from toto.people.models import Person
from toto.socialhub.models import Clearance
from toto.vault.models import BucketClearance, VaultFile

from .base import CyprianTestCase


class KeptBucketTests(CyprianTestCase):
    def setUp(self):
        super().setUp()
        self.clearance = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=self.clearance)
        self.doc = VaultFile.objects.create(
            owner=self.owner, title="report.html", file_type="html", bucket=self.bucket,
            file=SimpleUploadedFile("report.html",
                                    df.dumps(df.new_document("Report")).encode("utf-8")))
        self.client.force_login(self.owner)

    def hold(self, user):
        person, _ = Person.objects.get_or_create(user=user,
                                                 defaults={"display_name": user.username})
        person.clearances.add(self.clearance)

    def open(self):
        return self.client.get(reverse("cyprian:edit", args=[self.doc.pk]))

    def save(self, title="Changed"):
        return self.client.post(reverse("cyprian:save", args=[self.doc.pk]),
                                data=json.dumps({"document": {
                                    "title": title, "content": "<p>new</p>"}}),
                                content_type="application/json")

    def on_disk(self):
        self.doc.refresh_from_db()
        with self.doc.file.open("rb") as handle:
            return df.loads(handle.read().decode("utf-8"))

    def test_an_owner_without_the_clearance_cannot_open_the_writer(self):
        self.assertEqual(self.open().status_code, 404)

    def test_an_owner_without_the_clearance_cannot_save(self):
        self.assertEqual(self.save().status_code, 404)
        self.assertEqual(self.on_disk().title, "Report")

    def test_an_owner_without_the_clearance_cannot_read_the_source(self):
        self.assertEqual(self.client.get(
            reverse("cyprian:source", args=[self.doc.pk])).status_code, 404)

    def test_an_owner_without_the_clearance_cannot_fetch_a_rendition(self):
        self.assertEqual(self.client.get(
            reverse("cyprian:rendition", args=[self.doc.pk])).status_code, 404)

    def test_an_owner_holding_the_clearance_opens_and_saves(self):
        self.hold(self.owner)
        self.assertEqual(self.open().status_code, 200)
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.on_disk().title, "Changed")
        self.assertEqual(self.client.get(
            reverse("cyprian:source", args=[self.doc.pk])).status_code, 200)
        self.assertEqual(self.client.get(
            reverse("cyprian:rendition", args=[self.doc.pk])).status_code, 200)

    def test_a_lending_bridge_does_not_open_a_kept_bucket(self):
        # A wiki team member the bridge would let in, without the clearance.
        self.client.force_login(self.other)
        with mock.patch("toto.cyprian.views.bridge_may_edit", return_value=True):
            self.assertEqual(self.open().status_code, 404)
            self.assertEqual(self.save().status_code, 404)
        self.assertEqual(self.on_disk().title, "Report")

    def test_a_lending_bridge_opens_it_to_a_holder(self):
        self.hold(self.other)
        self.client.force_login(self.other)
        with mock.patch("toto.cyprian.views.bridge_may_edit", return_value=True):
            self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.on_disk().title, "Changed")
