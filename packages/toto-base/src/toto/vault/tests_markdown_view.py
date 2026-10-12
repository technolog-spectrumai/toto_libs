"""The Markdown viewer (2026-10-12): the Play button of a ``.md`` file and
the page behind it. The rendering itself is Morion's and runs in the browser
(``mdview/``; Morion's own tests are its tests); here: who gets the page, that
the text reaches it as data and never as markup, and what is refused.

    manage.py test toto.vault.tests_markdown_view
"""

import tempfile

from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.plugins import VaultPlayPlugin

NOTE = "# Plan\n\n<script>alert(1)</script>\n\n```mermaid\ngraph TD; A-->B;\n```\n"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="md-view-"),
                   VAULT_ROOT=tempfile.mkdtemp(prefix="md-view-vault-"))
class MarkdownViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.eve = User.objects.create_user("eve", password="pw")
        cls.bucket = Bucket.objects.create(name="Ada", slug="ada", owner=cls.ada)

    def make(self, title="plan.md", content=NOTE, **more):
        vault_file = VaultFile(title=title, owner=self.ada, bucket=self.bucket,
                               file_type="markdown", **more)
        vault_file.file.save(title, ContentFile(content.encode()), save=True)
        return vault_file

    def page(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("vault:markdown_view", args=[vault_file.pk]))

    def test_markdown_has_a_play_plugin_that_leads_to_the_page(self):
        plugin = VaultPlayPlugin.for_file_type("markdown")
        self.assertIsNotNone(plugin)
        note = self.make()
        self.assertEqual(plugin.get_play_url(note), reverse("vault:markdown_view", args=[note.pk]))

    def test_the_bundle_and_the_stylesheet_are_shipped(self):
        for name in ("vault/mdview/mdview.js", "vault/mdview/markdown.css"):
            self.assertIsNotNone(finders.find(name), name)

    def test_the_notes_text_reaches_the_page_as_data(self):
        response = self.page(self.ada, self.make())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["markdown_source"], NOTE)
        body = response.content.decode()
        self.assertIn('id="markdown-source" type="application/json"', body)
        self.assertIn("vault/mdview/mdview.js", body)
        # Never as markup: the note's script tag is escaped inside the data.
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("\\u003Cscript\\u003Ealert(1)", body)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_somebody_who_may_not_read_the_file_gets_what_a_missing_file_gets(self):
        self.assertEqual(self.page(self.eve, self.make()).status_code, 404)
        self.client.force_login(self.ada)
        self.assertEqual(self.client.get(reverse("vault:markdown_view", args=[987654])).status_code, 404)
        self.client.logout()
        self.assertIn(self.client.get(reverse("vault:markdown_view", args=[1])).status_code, (302, 403))

    def test_an_encrypted_note_and_a_large_one_are_not_read(self):
        from toto.vault import timeline_views

        sealed = self.page(self.ada, self.make("sealed.md", is_encrypted=True))
        self.assertEqual(sealed.context["markdown_refusal"], "encrypted")
        self.assertEqual(sealed.context["markdown_source"], "")
        old, timeline_views.MARKDOWN_MAX_BYTES = timeline_views.MARKDOWN_MAX_BYTES, 10
        try:
            large = self.page(self.ada, self.make("large.md"))
        finally:
            timeline_views.MARKDOWN_MAX_BYTES = old
        self.assertEqual(large.context["markdown_refusal"], "too_large")
        self.assertNotIn('id="markdown-source"', large.content.decode())

    def test_reading_a_note_is_in_its_history(self):
        from toto.vault.models import VaultEvent

        note = self.make()
        self.page(self.ada, note)
        self.assertTrue(VaultEvent.objects.filter(file_number=note.pk, kind="opened").exists())
