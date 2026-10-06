"""A map drawing in the vault: the file type ``geojson`` (2026-10-06).

A `.geojson` used to have no type of its own: an upload was ``json`` or
``text`` by what the browser called it. It is a type now, and it is text and
JSON, so every list that takes ``json`` as text takes it too. What is held:

* the type and its ending, in the model and in the one migration;
* an upload is typed by its ending, in any case, whatever the browser says,
  at the API door and the gateway door, and a bare ``application/geo+json``
  is the type too;
* it is text: the API reads, writes and creates one, and an empty one is
  GeoJSON's empty collection;
* a rename retypes a text file by its new ending
  (``VaultFile.type_after_rename``) at the page's door and the API's, and
  leaves alone what it must;
* a download is served as ``application/geo+json``, always as an attachment;
* a mirrored row keeps the type its peer sent.

    manage.py test toto.vault.tests_geojson
"""

import importlib
import json
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.api_views import EDITABLE_FILE_TYPES
from toto.vault.mirror import _stub_fields
from toto.vault.models import Bucket, FileGateway, VaultDirectory, VaultFile
from toto.vault.strategy.text import TextStrategy
from toto.vault.views import CREATABLE_TYPES, CreateEmptyFileView

User = get_user_model()

MIME = "application/geo+json"
EMPTY = '{\n  "type": "FeatureCollection",\n  "features": []\n}\n'
DRAWING = (b'{"type": "FeatureCollection", "features": [{"type": "Feature", "id": "p1", '
           b'"geometry": {"type": "Point", "coordinates": [18.6466, 54.352]}, '
           b'"properties": {"name": "D\xc5\x82ugi Targ", "note": "", "color": "#2563eb"}}]}\n')


class TheTypeTests(SimpleTestCase):
    def test_it_is_a_type_with_a_label_and_an_ending(self):
        self.assertEqual(dict(VaultFile.FILE_TYPES)["geojson"], "GeoJSON")
        self.assertEqual(VaultFile._EXT_MAP[".geojson"], "geojson")
        self.assertEqual([e for e, t in VaultFile._EXT_MAP.items() if t == "geojson"],
                         [".geojson"])
        self.assertLessEqual(len("geojson"), VaultFile._meta.get_field("file_type").max_length)

    def test_the_one_migration_lists_the_same_types(self):
        """Edited in place: no migration history is kept."""
        initial = importlib.import_module("toto.vault.migrations.0001_initial")
        [made] = [op for op in initial.Migration.operations
                  if getattr(op, "name", "") == "VaultFile" and hasattr(op, "fields")]
        choices = dict(made.fields)["file_type"].choices
        self.assertEqual([tuple(c) for c in choices], VaultFile.FILE_TYPES)

    def test_the_ending_decides_in_any_case_whatever_the_browser_says(self):
        for name in ("trip.geojson", "TRIP.GEOJSON", "Trip.GeoJson", "a.b.geojson",
                     "zażółć gęślą.geojson"):
            for mime in ("", MIME, "application/json", "text/plain",
                         "application/octet-stream"):
                with self.subTest(name=name, mime=mime):
                    self.assertEqual(VaultFile.detect_type(mime, name), "geojson")

    def test_the_mime_type_alone_names_it_and_plain_json_stays_json(self):
        self.assertEqual(VaultFile.detect_type(MIME), "geojson")
        self.assertEqual(VaultFile.detect_type("Application/Geo+JSON; charset=utf-8"), "geojson")
        self.assertEqual(VaultFile.detect_type(MIME, "export.bin"), "geojson")
        self.assertEqual(VaultFile.detect_type("application/json"), "json")
        self.assertEqual(VaultFile.detect_type(MIME, "data.json"), "json")
        self.assertEqual(VaultFile.detect_type("", "geojson"), "text")
        self.assertEqual(VaultFile.detect_type("", "trip.geojson.txt"), "text")

    def test_it_is_text_wherever_json_is(self):
        self.assertIn("json", EDITABLE_FILE_TYPES)
        self.assertIn("geojson", EDITABLE_FILE_TYPES)
        self.assertIsInstance(VaultFile(file_type="geojson").get_strategy(), TextStrategy)
        self.assertNotIn("geojson", VaultFile._BYTES_TYPES)
        self.assertIn("json", CreateEmptyFileView._ALLOWED)
        self.assertIn("geojson", CreateEmptyFileView._ALLOWED)
        self.assertIn(("geojson", ".geojson"), CREATABLE_TYPES)

    def test_an_empty_one_is_the_empty_collection(self):
        starter = CreateEmptyFileView._INITIAL["geojson"]
        self.assertEqual(starter, EMPTY)
        self.assertEqual(json.loads(starter), {"type": "FeatureCollection", "features": []})

    def test_a_download_says_what_it_is(self):
        self.assertEqual(VaultFile.DOWNLOAD_MIME, {"geojson": MIME})

    def test_a_mirrored_row_keeps_the_type_its_peer_sent(self):
        self.assertEqual(_stub_fields({"file_type": "geojson", "title": "trip.geojson"})
                         ["file_type"], "geojson")
        # A peer on an older library sends what it has, and that stays.
        self.assertEqual(_stub_fields({"file_type": "json", "title": "trip.geojson"})
                         ["file_type"], "json")


class TypeAfterRenameTests(SimpleTestCase):
    def after(self, title, file_type, new_title):
        return VaultFile(title=title, file_type=file_type).type_after_rename(new_title)

    def test_a_text_file_follows_its_new_ending(self):
        for title, file_type, new_title, expected in (
                ("trip.json", "json", "trip.geojson", "geojson"),
                ("trip.txt", "text", "Trip.GEOJSON", "geojson"),
                ("trip", "text", "trip.geojson", "geojson"),
                ("trip.geojson", "geojson", "trip.json", "json"),
                ("trip.geojson", "geojson", "trip.txt", "text"),
                ("notes.txt", "text", "notes.md", "markdown"),
                ("book.json", "json", "book.uson", "uson")):
            with self.subTest(title=title, new_title=new_title):
                self.assertEqual(self.after(title, file_type, new_title), expected)

    def test_everything_else_keeps_its_type(self):
        for title, file_type, new_title in (
                # The same ending: an older row typed json stays one.
                ("trip.geojson", "json", "journey.geojson"),
                ("trip.geojson", "text", "TRIP.GEOJSON"),
                # A title need not carry an ending, and an unknown one says nothing.
                ("trip.geojson", "geojson", "My trip"),
                ("trip.geojson", "geojson", "trip.geojson.bak"),
                ("notes.txt", "markdown", "notes2.txt"),
                # What a file's bytes are, a name does not change.
                ("photo.png", "image", "photo.geojson"),
                ("scan.pdf", "pdf", "scan.txt"),
                ("all.zip", "zip", "all.json"),
                ("trip.geojson", "geojson", "trip.pdf"),
                ("trip.geojson", "geojson", "trip.png"),
                ("trip.geojson", "geojson", "trip.zip"),
                ("trip.geojson", "geojson", "trip.mp3")):
            with self.subTest(title=title, new_title=new_title):
                self.assertEqual(self.after(title, file_type, new_title), file_type)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-geojson-"))
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.owner = User.objects.create_user("owner", password="pw")
        cls.bucket = Bucket.objects.create(name="Maps", slug="maps", owner=cls.owner)
        cls.folder = VaultDirectory.objects.create(name="Trips", bucket=cls.bucket,
                                                   owner=cls.owner)

    def setUp(self):
        self.client.force_login(self.owner)

    _n = 0

    def file(self, title, file_type, body=DRAWING, *, stored=None, **fields):
        type(self)._n += 1
        vault_file = VaultFile(owner=self.owner, title=title, key=f"k-{self._n}",
                               file_type=file_type, bucket=self.bucket, **fields)
        vault_file.file.save(stored or title, ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def post_json(self, url, payload, method="post"):
        return getattr(self.client, method)(url, data=json.dumps(payload),
                                            content_type="application/json")


class UploadDoorTests(_Fixture):
    def test_the_api_door_types_it_by_its_ending(self):
        for name, said in (("trip.geojson", "text/plain"), ("TRIP2.GEOJSON", "application/json"),
                           ("Trip3.GeoJson", "application/octet-stream"),
                           ("trip4.geojson", MIME)):
            with self.subTest(name=name):
                response = self.client.post(reverse("vault:api_file_upload"), {
                    "file": SimpleUploadedFile(name, DRAWING, content_type=said)})
                self.assertEqual(response.status_code, 201, response.content)
                answer = response.json()
                # This door keeps the title without its ending; the type is
                # what says the file is a drawing from then on.
                self.assertEqual((answer["title"], answer["file_type"]),
                                 (name.rsplit(".", 1)[0], "geojson"))
                self.assertIs(answer["is_editable"], True)
        # A .json is a .json, as before.
        response = self.client.post(reverse("vault:api_file_upload"), {
            "file": SimpleUploadedFile("data.json", b"{}", content_type="application/json")})
        self.assertEqual(response.json()["file_type"], "json")

    def test_the_gateway_door_types_it_and_takes_the_type_by_hand(self):
        FileGateway.objects.create(directory=self.folder, name="Drop", max_file_size=64)
        url = reverse("vault:gateway_upload", kwargs={"dir_pk": self.folder.pk})
        response = self.client.post(url, {"file": [
            SimpleUploadedFile("Route.GEOJSON", DRAWING, content_type="text/plain")]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(VaultFile.objects.get(title="Route.GEOJSON").file_type, "geojson")
        # The page's own select names the type for a drawing called .json.
        response = self.client.post(url, {"file_type": "geojson", "file": [
            SimpleUploadedFile("drawing.json", DRAWING, content_type="application/json")]})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(VaultFile.objects.get(title="drawing.json").file_type, "geojson")
        page = self.client.get(reverse("vault:gateway_page", kwargs={"dir_pk": self.folder.pk}))
        self.assertContains(page, '<option value="geojson">')


class TextDoorTests(_Fixture):
    def test_the_api_reads_and_saves_one_as_text(self):
        drawing = self.file("trip.geojson", "geojson")
        url = reverse("vault:api_file_content", args=[drawing.key])
        read = self.client.get(url)
        self.assertEqual(read.status_code, 200, read.content)
        self.assertEqual(read.json()["file_type"], "geojson")
        self.assertEqual(read.json()["content"], DRAWING.decode("utf-8"))
        saved = self.post_json(url, {"content": EMPTY}, "put")
        self.assertEqual(saved.status_code, 200, saved.content)
        with VaultFile.objects.get(pk=drawing.pk).file.open("rb") as handle:
            self.assertEqual(handle.read().decode("utf-8"), EMPTY)

    def test_the_api_takes_a_pushed_one_with_its_content(self):
        response = self.post_json(reverse("vault:api_file_create"), {
            "bucket_slug": "maps", "title": "pushed.geojson", "file_type": "geojson",
            "content": DRAWING.decode("utf-8")})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["file_type"], "geojson")
        self.assertIs(response.json()["is_editable"], True)

    @override_settings(VAULT_STORAGE_ONLY=False)
    def test_an_empty_one_made_by_the_api_is_the_empty_collection(self):
        response = self.post_json(reverse("vault:api_file_create"), {
            "bucket_slug": "maps", "title": "new.geojson", "file_type": "geojson"})
        self.assertEqual(response.status_code, 201, response.content)
        made = VaultFile.objects.get(title="new.geojson")
        self.assertEqual(made.file_type, "geojson")
        with made.file.open("rb") as handle:
            self.assertEqual(handle.read().decode("utf-8"), EMPTY)

    def test_the_list_page_knows_the_type(self):
        self.file("trip.geojson", "geojson")
        response = self.client.get(reverse("vault:public_list"))
        self.assertEqual(response.status_code, 200)
        # The rename dialog's select, from the model's list, and the icon.
        self.assertContains(response, '<option value="geojson">geojson</option>')
        self.assertContains(response, "geojson:'fa-map-location-dot'")
        [row] = [i for i in response.context["flat_items"] if i["t"] == "file"]
        self.assertEqual((row["title"], row["file_type"]), ("trip.geojson", "geojson"))


class RenameDoorTests(_Fixture):
    def rename(self, vault_file, title, **more):
        return self.client.post(reverse("vault:rename_file"),
                                {"file_pk": vault_file.pk, "title": title, **more})

    def typed(self, vault_file):
        fresh = VaultFile.objects.get(pk=vault_file.pk)
        return fresh.title, fresh.file_type

    def test_a_json_renamed_geojson_becomes_a_map_drawing(self):
        # The dialog posts the type the file has; a blank type is the same.
        for posted in ({"file_type": "json"}, {}):
            with self.subTest(posted=posted):
                drawing = self.file("trip.json", "json")
                response = self.rename(drawing, "Trip.GEOJSON", **posted)
                self.assertEqual(response.json(), {"ok": True, "title": "Trip.GEOJSON",
                                                   "file_type": "geojson"})
                self.assertEqual(self.typed(drawing), ("Trip.GEOJSON", "geojson"))

    def test_and_back(self):
        drawing = self.file("trip.geojson", "geojson")
        self.assertEqual(self.rename(drawing, "trip.json", file_type="geojson").json()
                         ["file_type"], "json")
        self.assertEqual(self.typed(drawing), ("trip.json", "json"))

    def test_another_type_chosen_with_the_name_wins(self):
        drawing = self.file("trip.json", "json")
        self.rename(drawing, "trip.geojson", file_type="text")
        self.assertEqual(self.typed(drawing), ("trip.geojson", "text"))

    def test_an_older_row_keeps_its_type_until_it_is_chosen(self):
        older = self.file("trip.geojson", "json")
        self.rename(older, "journey.geojson", file_type="json")
        self.assertEqual(self.typed(older), ("journey.geojson", "json"))
        # The dialog's select carries the type, and choosing it retypes.
        self.rename(older, "journey.geojson", file_type="geojson")
        self.assertEqual(self.typed(older), ("journey.geojson", "geojson"))

    def test_a_title_without_the_ending_keeps_the_type(self):
        drawing = self.file("trip.geojson", "geojson")
        self.rename(drawing, "My trip", file_type="geojson")
        self.assertEqual(self.typed(drawing), ("My trip", "geojson"))

    def test_a_picture_is_not_made_a_drawing_by_a_name(self):
        picture = self.file("photo.png", "image", b"\x89PNG\r\n")
        self.rename(picture, "photo.geojson", file_type="image")
        self.assertEqual(self.typed(picture), ("photo.geojson", "image"))

    @override_settings(VAULT_REFUSED_FILE_TYPES={"geojson"})
    def test_a_refused_type_is_not_let_in_by_an_ending(self):
        note = self.file("trip.json", "json")
        response = self.rename(note, "trip.geojson", file_type="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("geojson", response.json()["error"])
        self.assertEqual(self.typed(note), ("trip.json", "json"))

    def test_the_api_rename_retypes_the_same_way(self):
        drawing = self.file("trip.json", "json")
        url = reverse("vault:api_file_detail", args=[drawing.key])
        answer = self.post_json(url, {"title": "trip.geojson"}, "patch")
        self.assertEqual(answer.status_code, 200, answer.content)
        self.assertEqual((answer.json()["title"], answer.json()["file_type"]),
                         ("trip.geojson", "geojson"))
        self.assertEqual(self.typed(drawing), ("trip.geojson", "geojson"))
        # The same ending again changes nothing but the name.
        self.post_json(url, {"title": "journey.geojson"}, "patch")
        self.assertEqual(self.typed(drawing), ("journey.geojson", "geojson"))
        with self.settings(VAULT_REFUSED_FILE_TYPES={"markdown"}):
            refused = self.post_json(url, {"title": "journey.md"}, "patch")
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.typed(drawing), ("journey.geojson", "geojson"))


class DownloadTests(_Fixture):
    def assertServedAsADrawing(self, response):
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], MIME)
        self.assertTrue(response["Content-Disposition"].startswith("attachment"),
                        response["Content-Disposition"])
        self.assertEqual(b"".join(response.streaming_content), DRAWING)

    def test_the_download_door_serves_the_type_whatever_the_name_on_disk(self):
        # Stored under another name, as a file renamed after its upload is.
        for stored in ("trip.geojson", "trip.json", "trip.bin", "trip.html"):
            with self.subTest(stored=stored):
                drawing = self.file("trip.geojson", "geojson", stored=stored)
                self.assertServedAsADrawing(self.client.get(
                    reverse("vault:public_file", args=[self.bucket.slug, drawing.key])))

    def test_the_api_download_serves_it_too(self):
        drawing = self.file("trip.geojson", "geojson")
        self.assertServedAsADrawing(self.client.get(
            reverse("vault:api_file_download", args=[drawing.key])))

    def test_another_type_is_served_as_before(self):
        plain = self.file("data.json", "json", b"{}")
        response = self.client.get(
            reverse("vault:public_file", args=[self.bucket.slug, plain.key]))
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))
        page = self.file("page.html", "html", b"<p>x</p>")
        response = self.client.get(
            reverse("vault:public_file", args=[self.bucket.slug, page.key]))
        self.assertTrue(response["Content-Disposition"].startswith("attachment"))

    def test_a_locked_one_unlocked_for_download_is_served_as_a_drawing(self):
        locked = self.file("trip.geojson", "geojson", is_encrypted=True)

        class Unlocks:
            def decrypt_to_bytes(self, file_instance, password=None):
                return DRAWING, "application/octet-stream"

        with patch.object(VaultFile, "get_strategy", return_value=Unlocks()):
            response = self.client.post(reverse("vault:download_encrypted"),
                                        {"file_pk": locked.pk, "password": "pw"})
        self.assertServedAsADrawing(response)
