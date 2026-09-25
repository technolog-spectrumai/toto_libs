"""Who may change a location object, and what the map lists (2026-09-25)."""

import json

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations.models import HAS_GIS, Address, Territory

User = get_user_model()


class LocationWriteAuthorizationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cls.ada = User.objects.create_user("ada", password="x")
        cls.bob = User.objects.create_user("bob", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.mine = Address.objects.create(locality_name="Kraków", street="Floriańska",
                                          created_by=cls.ada)
        cls.legacy = Address.objects.create(locality_name="Gdańsk", street="Długa")

    def note(self, obj, text="hello"):
        return self.client.post(reverse("locations:note_save", args=["address", obj.pk]),
                                {"note": text})

    def metadata(self, obj):
        return self.client.post(reverse("locations:metadata_save", args=["address", obj.pk]),
                                {"metadata": json.dumps({"k": 1}), "format": "json"})

    def test_the_creator_writes_their_own_address(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.note(self.mine).status_code, 302)
        self.assertEqual(self.metadata(self.mine).status_code, 200)
        self.mine.refresh_from_db()
        self.assertEqual((self.mine.note, self.mine.metadata), ("hello", {"k": 1}))

    def test_another_member_is_refused(self):
        self.client.force_login(self.bob)
        self.assertEqual(self.note(self.mine, "theirs").status_code, 403)
        self.assertEqual(self.metadata(self.mine).status_code, 403)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.note, "")

    def test_a_row_without_a_creator_is_staffs(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.note(self.legacy).status_code, 403)
        self.client.force_login(self.staff)
        self.assertEqual(self.note(self.legacy).status_code, 302)
        self.assertEqual(self.metadata(self.legacy).status_code, 200)

    def test_the_detail_page_says_who_may_edit(self):
        self.client.force_login(self.bob)
        response = self.client.get(reverse("locations:location_detail", args=["address", self.mine.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])
        self.assertContains(response, "Only its creator or staff may change this.")
        self.client.force_login(self.ada)
        response = self.client.get(reverse("locations:location_detail", args=["address", self.mine.pk]))
        self.assertTrue(response.context["can_edit"])

    def test_access_rules(self):
        from toto.locations.access import may_import_layer, may_write

        self.assertTrue(may_write(self.ada, self.mine))
        self.assertFalse(may_write(self.bob, self.mine))
        self.assertTrue(may_write(self.staff, self.mine))
        self.assertFalse(may_write(self.ada, self.legacy))
        self.assertTrue(may_import_layer(self.staff))
        self.assertFalse(may_import_layer(self.ada))


class LayerImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cls.ada = User.objects.create_user("ada", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)

    def test_a_member_may_not_import_a_layer(self):
        if not HAS_GIS:
            self.skipTest("layer import is GIS-only")
        self.client.force_login(self.ada)
        response = self.client.post(reverse("locations:api_import_layer"), {"vault_file_id": "1"})
        self.assertEqual(response.status_code, 403)

    def test_staff_may_not_read_a_file_they_cannot_see(self):
        if not HAS_GIS:
            self.skipTest("layer import is GIS-only")
        from toto.vault.models import Bucket, VaultFile

        bucket = Bucket.objects.create(owner=self.ada, name="A", slug="a-ada", storage_backend="local")
        vf = VaultFile(owner=self.ada, title="x.json", file_type="json", bucket=bucket)
        vf.file.save("x.json", ContentFile(b'{"type":"FeatureCollection","features":[]}'), save=False)
        vf.save()
        not_superuser = User.objects.create_user("op", password="x", is_staff=True)
        self.client.force_login(not_superuser)
        response = self.client.post(reverse("locations:api_import_layer"), {"vault_file_id": vf.pk})
        self.assertEqual(response.status_code, 404)
