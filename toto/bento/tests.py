from cryptography.exceptions import InvalidTag
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from toto.people.models import Person

from .models import Category, IdeaBox, IdeaLink


class BoxPageRenderTests(TestCase):
    """The list and detail pages read the folded `properties` keys (body,
    is_concept, source_*, quote) through the model accessors, so render them
    end-to-end to guard the redesign."""

    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="T", author="A", publication_year=2024, active=True)
        self.client = Client()
        self.category = Category.objects.create(name="Principle")
        self.box = IdeaBox.objects.create(
            label="Stories beat facts",
            category=self.category,
            properties={
                "body": "People remember stories.",
                "is_concept": False,
                "source_title": "Made to Stick",
                "source_type": "book",
                "source_url": "https://example.com",
                "quote": "A memorable quote.",
                "rating": 5,
            },
        )
        self.concept = IdeaBox.objects.create(
            label="Memory", properties={"is_concept": True}
        )
        IdeaLink.objects.create(from_box=self.box, to_box=self.concept, label="about")

    def test_box_list_renders(self):
        res = self.client.get(reverse("bento:box_list"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Stories beat facts", html)
        self.assertIn("Made to Stick", html)  # source_title from properties

    def test_box_list_concept_filter(self):
        res = self.client.get(reverse("bento:box_list"), {"concept": "yes"})
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Memory", html)
        self.assertNotIn("Stories beat facts", html)

    def test_box_list_search_hits_properties_body(self):
        res = self.client.get(reverse("bento:box_list"), {"q": "remember"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Stories beat facts", res.content.decode())

    def test_box_detail_renders(self):
        res = self.client.get(reverse("bento:box_detail", args=[self.box.pk]))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Stories beat facts", html)
        self.assertIn("People remember stories.", html)  # body
        self.assertIn("A memorable quote.", html)         # quote


class IdeaBoxLockTests(TestCase):
    def setUp(self):
        self.box = IdeaBox.objects.create(label="Secret note", properties={"body": "Hidden content"})

    def test_lock_encrypts_body(self):
        self.box.lock("pass123")
        self.box.refresh_from_db()
        self.assertTrue(self.box.is_locked)
        self.assertEqual(self.box.body, "")
        self.assertIsNotNone(self.box.encrypted_body)
        self.assertIsNotNone(self.box.lock_nonce)
        self.assertIsNotNone(self.box.lock_salt)

    def test_unlock_restores_body(self):
        self.box.lock("pass123")
        self.box.unlock("pass123")
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_locked)
        self.assertEqual(self.box.body, "Hidden content")
        self.assertIsNone(self.box.encrypted_body)
        self.assertIsNone(self.box.lock_nonce)
        self.assertIsNone(self.box.lock_salt)

    def test_unlock_wrong_password_raises(self):
        self.box.lock("pass123")
        with self.assertRaises(InvalidTag):
            self.box.unlock("wrong")

    def test_lock_already_locked_raises(self):
        self.box.lock("pass123")
        with self.assertRaises(ValueError):
            self.box.lock("pass123")

    def test_unlock_not_locked_raises(self):
        with self.assertRaises(ValueError):
            self.box.unlock("pass123")

    def test_lock_empty_password_raises(self):
        with self.assertRaises(ValueError):
            self.box.lock("")

    def test_different_ciphertexts_for_same_input(self):
        box2 = IdeaBox.objects.create(label="Copy", properties={"body": "Hidden content"})
        self.box.lock("pass123")
        box2.lock("pass123")
        self.assertNotEqual(bytes(self.box.lock_nonce), bytes(box2.lock_nonce))
        self.assertNotEqual(bytes(self.box.encrypted_body), bytes(box2.encrypted_body))


class BoxLockViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester", password="pw")
        Person.objects.create(user=self.user, is_federal_agent=True)
        self.client = Client()
        self.client.force_login(self.user)
        self.box = IdeaBox.objects.create(label="Note", properties={"body": "Secret"})

    def _lock_url(self):
        return reverse("bento:box_lock", args=[self.box.pk])

    def _unlock_url(self):
        return reverse("bento:box_unlock", args=[self.box.pk])

    def test_lock_missing_password_returns_400(self):
        res = self.client.post(self._lock_url(), {})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_lock_success(self):
        res = self.client.post(self._lock_url(), {"password": "secret"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.box.refresh_from_db()
        self.assertTrue(self.box.is_locked)

    def test_lock_already_locked_returns_400(self):
        self.box.lock("secret")
        res = self.client.post(self._lock_url(), {"password": "secret"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["ok"])

    def test_unlock_missing_password_returns_400(self):
        self.box.lock("secret")
        res = self.client.post(self._unlock_url(), {})
        self.assertEqual(res.status_code, 400)

    def test_unlock_correct_password_success(self):
        self.box.lock("secret")
        res = self.client.post(self._unlock_url(), {"password": "secret"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_locked)
        self.assertEqual(self.box.body, "Secret")

    def test_unlock_wrong_password_returns_400(self):
        self.box.lock("secret")
        res = self.client.post(self._unlock_url(), {"password": "nope"})
        self.assertEqual(res.status_code, 400)
        data = res.json()
        self.assertFalse(data["ok"])
        self.assertIn("error", data)

    def test_unlock_not_locked_returns_400(self):
        res = self.client.post(self._unlock_url(), {"password": "secret"})
        self.assertEqual(res.status_code, 400)

    def test_lock_get_not_allowed(self):
        res = self.client.get(self._lock_url())
        self.assertEqual(res.status_code, 405)

    def test_unlock_get_not_allowed(self):
        res = self.client.get(self._unlock_url())
        self.assertEqual(res.status_code, 405)


class BoxLockPermissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("civilian", password="pw")
        # No Person / is_federal_agent=False
        self.client = Client()
        self.client.force_login(self.user)
        self.box = IdeaBox.objects.create(label="Note", properties={"body": "Secret"})

    def test_non_agent_cannot_lock(self):
        res = self.client.post(reverse("bento:box_lock", args=[self.box.pk]), {"password": "pw"})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.json()["ok"])
        self.box.refresh_from_db()
        self.assertFalse(self.box.is_locked)

    def test_non_agent_cannot_unlock(self):
        # Lock via model directly, bypassing view
        self.box.lock("pw")
        res = self.client.post(reverse("bento:box_unlock", args=[self.box.pk]), {"password": "pw"})
        self.assertEqual(res.status_code, 403)
        self.assertFalse(res.json()["ok"])
        self.box.refresh_from_db()
        self.assertTrue(self.box.is_locked)

    def test_non_agent_with_false_flag_cannot_lock(self):
        Person.objects.create(user=self.user, is_federal_agent=False)
        res = self.client.post(reverse("bento:box_lock", args=[self.box.pk]), {"password": "pw"})
        self.assertEqual(res.status_code, 403)


class SerializeBoxLockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tester2", password="pw")
        Person.objects.create(user=self.user, is_federal_agent=True)
        self.client = Client()
        self.client.force_login(self.user)

    def test_api_boxes_hides_body_when_locked(self):
        box = IdeaBox.objects.create(label="Private", properties={"body": "Top secret"})
        box.lock("pw")
        res = self.client.get(reverse("bento:api_boxes"))
        data = res.json()
        result = next(r for r in data["results"] if r["id"] == box.pk)
        self.assertIsNone(result["properties"]["body"])
        self.assertTrue(result["is_locked"])

    def test_api_boxes_shows_body_when_unlocked(self):
        box = IdeaBox.objects.create(label="Public", properties={"body": "Visible"})
        res = self.client.get(reverse("bento:api_boxes"))
        data = res.json()
        result = next(r for r in data["results"] if r["id"] == box.pk)
        self.assertEqual(result["properties"]["body"], "Visible")
        self.assertFalse(result["is_locked"])


class MetadataAceEditorTests(TestCase):
    """The node/link `properties` field is edited as JSON via an ACE editor."""

    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="T", author="A", publication_year=2024, active=True)
        self.user = User.objects.create_user("aceuser", password="pw")
        self.client = Client()
        self.client.force_login(self.user)

    def test_box_create_renders_ace_editor(self):
        res = self.client.get(reverse("bento:box_create"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('class="metadata-ace"', html)
        self.assertIn('id_properties_ace', html)
        self.assertIn('vendor/ace/ace.', html)
        self.assertIn("Metadata (JSON)", html)

    def test_box_update_seeds_existing_properties(self):
        box = IdeaBox.objects.create(label="C", properties={"rating": 4})
        res = self.client.get(reverse("bento:box_update", args=[box.pk]))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('class="metadata-ace"', html)
        self.assertIn("rating", html)

    def test_link_create_renders_ace_editor(self):
        res = self.client.get(reverse("bento:link_create"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn('class="metadata-ace"', html)
        self.assertIn('vendor/ace/ace.', html)

    def test_box_update_saves_json_metadata(self):
        box = IdeaBox.objects.create(label="C")
        res = self.client.post(
            reverse("bento:box_update", args=[box.pk]),
            {"label": "C", "properties": '{"rating": 5, "tags": ["a"]}'},
        )
        self.assertEqual(res.status_code, 302)
        box.refresh_from_db()
        self.assertEqual(box.properties, {"rating": 5, "tags": ["a"]})

    def test_box_update_rejects_invalid_json_metadata(self):
        box = IdeaBox.objects.create(label="C", properties={"keep": True})
        res = self.client.post(
            reverse("bento:box_update", args=[box.pk]),
            {"label": "C", "properties": "{not json}"},
        )
        self.assertEqual(res.status_code, 200)  # re-rendered with errors, not saved
        box.refresh_from_db()
        self.assertEqual(box.properties, {"keep": True})
