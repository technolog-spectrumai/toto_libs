"""The GeoJSON tab (2026-10-12): a GeoJSON file of the vault that the viewer
may read, drawn on the map; nobody else's file, no encrypted one, nothing
that is not GeoJSON.

    manage.py test toto.locations.tests_geojson_tab
"""

import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile

SHAPE = {"type": "FeatureCollection", "features": [
    {"type": "Feature", "properties": {"name": "Hall"},
     "geometry": {"type": "Point", "coordinates": [21.0122, 52.2297]}}]}


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="geojson-tab-"),
                   VAULT_ROOT=tempfile.mkdtemp(prefix="geojson-tab-vault-"))
class GeoJsonTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.eve = User.objects.create_user("eve", password="pw")
        cls.bucket = Bucket.objects.create(name="Ada", slug="ada", owner=cls.ada)

    def make(self, title, content, **more):
        vault_file = VaultFile(title=title, owner=self.ada, bucket=self.bucket, **more)
        vault_file.file.save(title, ContentFile(content if isinstance(content, bytes)
                                                else json.dumps(content).encode()), save=True)
        return vault_file

    def door(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("locations:geojson_data", args=[vault_file.pk]))

    def test_the_tab_lists_the_viewers_json_files(self):
        self.make("places.geojson", SHAPE)
        self.make("notes.txt", b"hello")
        self.client.force_login(self.ada)
        response = self.client.get(reverse("locations:geojson"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([f["title"] for f in response.context["geojson_files"]], ["places.geojson"])
        body = response.content.decode()
        self.assertIn('data-testid="locations-tab-geojson"', body)
        self.assertIn("places.geojson", body)
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(reverse("locations:geojson")).context["geojson_files"], [])

    def test_the_vaults_play_button_opens_the_tab_on_the_file(self):
        from toto.vault.plugins import VaultPlayPlugin

        places = self.make("places.geojson", SHAPE)
        VaultFile.objects.filter(pk=places.pk).update(file_type="geojson")
        plugin = VaultPlayPlugin.for_file_type("geojson")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.get_play_url(places), reverse("locations:geojson") + f"?file={places.pk}")
        self.assertTrue(plugin.is_open_to(self.ada))
        self.client.force_login(self.ada)
        response = self.client.get(plugin.get_play_url(places))
        self.assertEqual(response.context["geojson_chosen"], places.pk)
        self.assertIn(f'<option value="{places.pk}" selected>', response.content.decode())
        # Somebody else's file, or a number that is no file: nothing chosen.
        self.client.force_login(self.eve)
        self.assertEqual(self.client.get(plugin.get_play_url(places)).context["geojson_chosen"], 0)
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("locations:geojson") + "?file=x").context["geojson_chosen"], 0)

    def test_a_file_one_may_read_comes_back_as_geojson(self):
        answer = self.door(self.ada, self.make("places.geojson", SHAPE))
        self.assertEqual(answer.status_code, 200)
        self.assertEqual(answer.json(), {"title": "places.geojson", "geojson": SHAPE})
        self.assertEqual(answer["Cache-Control"], "no-store")

    def test_somebody_elses_file_is_as_missing_as_no_file(self):
        self.assertEqual(self.door(self.eve, self.make("places.geojson", SHAPE)).status_code, 404)
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("locations:geojson_data", args=[987654])).status_code, 404)

    def test_what_is_not_geojson_is_refused(self):
        for title, content in (("list.json", [1, 2]), ("other.json", {"type": "Recipe"}),
                               ("broken.json", b"{not json"), ("bytes.json", b"\xff\xfe\x00")):
            with self.subTest(title=title):
                self.assertEqual(self.door(self.ada, self.make(title, content)).status_code, 400)

    def test_an_encrypted_file_and_a_large_one_are_refused(self):
        from toto.locations import views

        self.assertEqual(self.door(self.ada, self.make("sealed.geojson", SHAPE, is_encrypted=True)).status_code, 422)
        with override_settings():
            old, views.GEOJSON_MAX_BYTES = views.GEOJSON_MAX_BYTES, 10
            try:
                self.assertEqual(self.door(self.ada, self.make("big.geojson", SHAPE)).status_code, 413)
            finally:
                views.GEOJSON_MAX_BYTES = old

    def test_signed_out_there_is_no_door(self):
        vault_file = self.make("places.geojson", SHAPE)
        self.assertIn(self.client.get(reverse("locations:geojson_data", args=[vault_file.pk])).status_code, (302, 403))
