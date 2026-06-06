from cryptography.exceptions import InvalidTag
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse

from toto.events.models import EventCategory
from toto.people.models import Person

from .models import Category, IdeaBox, IdeaLink, SubjectReference


class BoxPageRenderTests(TestCase):
    """The list, detail, and relations pages read the folded `properties` keys
    (body, source_*, quote) through the model accessors, so render them
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
                "source_title": "Made to Stick",
                "source_type": "book",
                "source_url": "https://example.com",
                "quote": "A memorable quote.",
                "rating": 5,
            },
        )
        self.other = IdeaBox.objects.create(label="Memory", properties={"body": "recall"})
        IdeaLink.objects.create(from_box=self.box, to_box=self.other, label="about")

    def test_box_list_renders(self):
        res = self.client.get(reverse("bento:box_list"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Stories beat facts", html)   # label
        self.assertNotIn("Made to Stick", html)     # source is no longer shown in the list

    def test_link_list_renders(self):
        res = self.client.get(reverse("bento:link_list"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("about", html)               # relation label
        self.assertIn("Stories beat facts", html)  # from box
        self.assertIn("Memory", html)              # to box

    def test_link_list_search(self):
        res = self.client.get(reverse("bento:link_list"), {"q": "Memory"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("about", res.content.decode())

    def test_box_list_search_hits_properties_body(self):
        res = self.client.get(reverse("bento:box_list"), {"q": "remember"})
        self.assertEqual(res.status_code, 200)
        self.assertIn("Stories beat facts", res.content.decode())

    def test_box_detail_renders(self):
        res = self.client.get(reverse("bento:box_detail", args=[self.box.pk]))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("Stories beat facts", html)  # label

    def test_box_detail_has_no_body_source_quote_panels(self):
        res = self.client.get(reverse("bento:box_detail", args=[self.box.pk]))
        html = res.content.decode()
        # body / source / quote live only inside the read-only properties JSON viewer now,
        # not in dedicated panels (Source used fa-book-open, Quote used fa-quote-left).
        self.assertNotIn("fa-book-open", html)
        self.assertNotIn("fa-quote-left", html)

    def test_box_detail_renders_readonly_ace_properties(self):
        res = self.client.get(reverse("bento:box_detail", args=[self.box.pk]))
        html = res.content.decode()
        # Properties are shown as a read-only ACE JSON viewer seeded via json_script.
        self.assertIn('class="metadata-ace-view"', html)
        self.assertIn('id="box_properties_json"', html)
        self.assertIn("vendor/ace/ace.", html)


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


class SubjectReferenceTests(TestCase):
    """SubjectReference is an edge from a box out to an external model."""

    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="T", author="A", publication_year=2024, active=True)
        self.client = Client()
        self.box = IdeaBox.objects.create(label="Anchor box")
        self.category = EventCategory.objects.create(name="Workshops")
        self.ct = ContentType.objects.get_for_model(EventCategory)

    def _make_ref(self, label="references"):
        return SubjectReference.objects.create(
            box=self.box, content_type=self.ct, object_id=str(self.category.pk), label=label,
        )

    def test_subject_resolves(self):
        ref = self._make_ref()
        self.assertEqual(ref.subject, self.category)
        self.assertIn("Workshops", ref.subject_label)
        self.assertIn("Workshops", str(ref))

    def test_list_renders(self):
        self._make_ref("about")
        res = self.client.get(reverse("bento:subject_reference_list"))
        self.assertEqual(res.status_code, 200)
        html = res.content.decode()
        self.assertIn("about", html)
        self.assertIn("Anchor box", html)
        self.assertIn("Workshops", html)

    def test_options_endpoint_lists_subjects(self):
        res = self.client.get(reverse("bento:api_subject_options"), {"content_type": self.ct.pk})
        self.assertEqual(res.status_code, 200)
        options = res.json()["options"]
        self.assertTrue(
            any(o["value"] == str(self.category.pk) and "Workshops" in o["label"] for o in options)
        )

    def test_options_endpoint_rejects_disallowed_type(self):
        ct = ContentType.objects.get_for_model(IdeaBox)  # not an allowed target
        res = self.client.get(reverse("bento:api_subject_options"), {"content_type": ct.pk})
        self.assertEqual(res.json()["options"], [])

    def test_create_view(self):
        res = self.client.post(reverse("bento:subject_reference_create"), {
            "box": self.box.pk,
            "content_type": self.ct.pk,
            "object_id": str(self.category.pk),
            "label": "about",
            "properties": "{}",
        })
        self.assertEqual(res.status_code, 302)
        ref = SubjectReference.objects.get()
        self.assertEqual(ref.box, self.box)
        self.assertEqual(ref.subject, self.category)
        self.assertEqual(ref.label, "about")

    def test_create_rejects_nonexistent_object(self):
        res = self.client.post(reverse("bento:subject_reference_create"), {
            "box": self.box.pk,
            "content_type": self.ct.pk,
            "object_id": "999999",
            "label": "",
            "properties": "{}",
        })
        self.assertEqual(res.status_code, 200)  # re-rendered with error
        self.assertFalse(SubjectReference.objects.exists())

    def test_create_rejects_disallowed_content_type(self):
        ct = ContentType.objects.get_for_model(IdeaBox)
        res = self.client.post(reverse("bento:subject_reference_create"), {
            "box": self.box.pk,
            "content_type": ct.pk,
            "object_id": str(self.box.pk),
            "label": "",
            "properties": "{}",
        })
        self.assertEqual(res.status_code, 200)
        self.assertFalse(SubjectReference.objects.exists())

    def test_update_view(self):
        ref = self._make_ref("about")
        res = self.client.post(reverse("bento:subject_reference_update", args=[ref.pk]), {
            "box": self.box.pk,
            "content_type": self.ct.pk,
            "object_id": str(self.category.pk),
            "label": "located at",
            "properties": "{}",
        })
        self.assertEqual(res.status_code, 302)
        ref.refresh_from_db()
        self.assertEqual(ref.label, "located at")

    def test_delete_view(self):
        ref = self._make_ref()
        res = self.client.post(reverse("bento:subject_reference_delete", args=[ref.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(SubjectReference.objects.filter(pk=ref.pk).exists())

    def test_box_detail_shows_references(self):
        self._make_ref("about")
        res = self.client.get(reverse("bento:box_detail", args=[self.box.pk]))
        html = res.content.decode()
        self.assertIn("References", html)
        self.assertIn("Workshops", html)
