"""The NeoJSON editor's doors follow the bucket's clearances (2026-10-01).

Open, save and the load into Neo4j fetched the file by `owner=` alone, so an
owner who lacks their bucket's clearance still opened and saved there —
against "no owner bypass", which the ACE editor, cyprian and sketch have
followed since 2026-09-30 (`toto.editor.tests_clearance`). They ask
`access.gate_by_bucket` first now: a file hidden by its bucket is missing
(404), to its owner too.

RequestFactory, not the url: the app is retired on zenobia (not installed, not
routed), and a direct call is the same test on a host that mounts it. `toto`
is a namespace package: run as `manage.py test toto.neo_editor.tests_clearance`.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404, HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import include, path

from toto.core.models import Platform
from toto.neo_editor import views
from toto.people.models import Person
from toto.ravioli import neojson
from toto.socialhub.models import Clearance
from toto.vault.models import Bucket, BucketClearance, VaultFile

User = get_user_model()

# The editor page reverses its own save and load urls; a host that does not
# route the app finds them here (ROOT_URLCONF below).
urlpatterns = [path("neo/", include("toto.neo_editor.urls"))]

FIRST = neojson.dumps(neojson.new_graph())
SECOND = neojson.dumps(neojson.from_ravioli(
    [{"id": "n1", "labels": ["Person"], "props": {"name": "Alice"}}], []))


def _shown(request, template_name, context):
    # The template is missing where the app is not installed; what the door
    # hands it is what these tests are about.
    return HttpResponse(context["content"])


@override_settings(ROOT_URLCONF=__name__, STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})
class _Doors(TestCase):
    def setUp(self):
        # The file's bytes are a real file, and the deployed MEDIA_ROOT is a
        # root-owned bind mount. One directory per test.
        media = tempfile.mkdtemp(prefix="neo-editor-test-")
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        # PageProcessor answers 404 without an active Platform.
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)

        self.owner = User.objects.create_user("owner", password="pw")
        self.other = User.objects.create_user("colleague", password="pw")
        self.bucket = Bucket.objects.create(name="Graphs", slug="graphs",
                                            owner=self.owner, storage_backend="local")
        self.file = self.make(self.bucket)

        shown = mock.patch.object(views, "render", _shown)
        shown.start()
        self.addCleanup(shown.stop)

    def make(self, bucket) -> VaultFile:
        return VaultFile.objects.create(
            owner=self.owner, title="g.neojson", file_type="neojson", bucket=bucket,
            file=SimpleUploadedFile("g.neojson", FIRST.encode("utf-8")))

    def call(self, view, user, data=None):
        factory = RequestFactory()
        request = factory.get("/x/") if data is None else factory.post("/x/", data)
        request.user = user
        try:
            return view(request, self.file.pk)
        except Http404:
            return HttpResponse(status=404)

    def open(self, user=None):
        return self.call(views.neojson_editor_view, user or self.owner)

    def save(self, content, user=None):
        return self.call(views.neojson_save_view, user or self.owner, {"content": content})

    def load(self, user=None):
        # The sync engine is kept out, so a door that lets the request through
        # answers that it is missing, whatever the host installs.
        with mock.patch.dict(sys.modules, {"toto.sql_neo4j_sync": None}):
            return self.call(views.neojson_load_view, user or self.owner,
                             {"content": SECOND, "mode": "merge"})

    def on_disk(self) -> str:
        self.file.refresh_from_db()
        with self.file.file.open("rb") as handle:
            return handle.read().decode("utf-8")

    def assert_owner_works(self):
        opened = self.open()
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.content.decode("utf-8"), FIRST)
        self.assertEqual(self.save(SECOND).status_code, 200)
        self.assertEqual(self.on_disk(), SECOND)
        loaded = self.load()
        self.assertEqual(loaded.status_code, 400)
        self.assertIn("not installed", json.loads(loaded.content)["error"])

    def assert_missing_to(self, user):
        self.assertEqual(self.open(user).status_code, 404)
        self.assertEqual(self.save(SECOND, user).status_code, 404)
        self.assertEqual(self.load(user).status_code, 404)
        self.assertEqual(self.on_disk(), FIRST)


class OpenBucketTests(_Doors):
    """A bucket with no clearance: the owner's rule, as it always was."""

    def test_the_owner_opens_saves_and_loads(self):
        self.assert_owner_works()

    def test_a_colleague_finds_nothing(self):
        self.assert_missing_to(self.other)

    def test_a_file_in_no_bucket_is_its_owner_s(self):
        self.file = self.make(None)
        self.assert_owner_works()


class KeptBucketTests(_Doors):
    """A bucket kept to a clearance: its holders only, the owner included."""

    def setUp(self):
        super().setUp()
        self.clearance = Clearance.objects.create(name="payroll", slug="payroll")
        BucketClearance.objects.create(bucket=self.bucket, clearance=self.clearance)

    def hold(self, user):
        person, _ = Person.objects.get_or_create(user=user,
                                                 defaults={"display_name": user.username})
        person.clearances.add(self.clearance)

    def test_an_owner_without_the_clearance_cannot_open_the_file(self):
        self.assertEqual(self.open().status_code, 404)

    def test_an_owner_without_the_clearance_cannot_save_it(self):
        self.assertEqual(self.save(SECOND).status_code, 404)
        self.assertEqual(self.on_disk(), FIRST)

    def test_an_owner_without_the_clearance_cannot_load_it_into_the_graph(self):
        self.assertEqual(self.load().status_code, 404)

    def test_an_owner_holding_the_clearance_opens_saves_and_loads(self):
        self.hold(self.owner)
        self.assert_owner_works()

    def test_the_clearance_opens_nothing_to_a_colleague(self):
        # The gate narrows the owner's door; it never widens it to a holder.
        self.hold(self.other)
        self.assert_missing_to(self.other)
