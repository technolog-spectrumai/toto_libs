"""The deck player (2026-10-12): the Play button of a ``.pxml`` presentation
and the page behind it. The slides are built and scrubbed in the browser by
Morion's code (``deckview/``; Morion's own tests are its tests); here: who
gets the page, that the text reaches it as data, and what is refused.

    manage.py test toto.vault.tests_deck_view
"""

import tempfile

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.plugins import VaultPlayPlugin

DECK = ('<?xml version="1.0" encoding="utf-8"?>\n<presentation version="2" title="Plan">\n'
        '  <slide id="s1" layout="title-content">\n    <title>One</title>\n'
        '    <block id="b1" type="text" slot="body"><![CDATA[<script>alert(1)</script>]]></block>\n'
        '  </slide>\n</presentation>\n')


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="deck-view-"),
                   VAULT_ROOT=tempfile.mkdtemp(prefix="deck-view-vault-"))
class DeckViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.eve = User.objects.create_user("eve", password="pw")
        cls.bucket = Bucket.objects.create(name="Ada", slug="ada", owner=cls.ada)

    def make(self, title="plan.pxml", content=DECK, file_type="pxml", **more):
        vault_file = VaultFile(title=title, owner=self.ada, bucket=self.bucket,
                               file_type=file_type, **more)
        vault_file.file.save(title, ContentFile(content.encode()), save=True)
        return vault_file

    def page(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("vault:deck_view", args=[vault_file.pk]))

    def test_a_presentation_has_a_play_plugin_that_leads_to_the_page(self):
        if apps.is_installed("toto.memo"):
            self.skipTest("toto.memo plays presentations on this host")
        deck = self.make()
        for file_type in ("pxml", "presentation"):
            plugin = VaultPlayPlugin.for_file_type(file_type)
            self.assertIsNotNone(plugin, file_type)
            self.assertEqual(plugin.get_play_url(deck), reverse("vault:deck_view", args=[deck.pk]))

    def test_the_bundle_and_its_stylesheet_are_shipped(self):
        for name in ("vault/deckview/deckview.js", "vault/deckview/deckview.css"):
            self.assertIsNotNone(finders.find(name), name)

    def test_the_decks_text_reaches_the_page_as_data(self):
        response = self.page(self.ada, self.make())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["deck_source"], DECK)
        body = response.content.decode()
        self.assertIn('id="deck-source" type="application/json"', body)
        self.assertIn("vault/deckview/deckview.js", body)
        self.assertIn('data-testid="deck-fullscreen"', body)
        # The page knows where KaTeX is, for a deck with a formula (2026-10-12).
        self.assertIn("vendor/katex/katex.min.js", body)
        self.assertIn("TotoDeck.hasFormulas", body)
        self.assertNotIn("<script>alert(1)</script>", body)          # escaped inside the data
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(self.client.post(reverse("vault:deck_view", args=[1])).status_code, 405)

    def test_who_may_not_read_the_file_and_what_is_no_deck(self):
        self.assertEqual(self.page(self.eve, self.make()).status_code, 404)
        self.assertEqual(self.page(self.ada, self.make("note.md", "# hi", file_type="markdown")).status_code, 404)
        self.client.logout()
        self.assertIn(self.client.get(reverse("vault:deck_view", args=[1])).status_code, (302, 403))

    def test_an_encrypted_deck_and_a_large_one_are_not_read(self):
        from toto.vault import timeline_views

        sealed = self.page(self.ada, self.make("sealed.pxml", is_encrypted=True))
        self.assertEqual((sealed.context["deck_refusal"], sealed.context["deck_source"]), ("encrypted", ""))
        self.assertNotIn('data-testid="deck-fullscreen"', sealed.content.decode())
        old, timeline_views.DECK_MAX_BYTES = timeline_views.DECK_MAX_BYTES, 5
        try:
            large = self.page(self.ada, self.make("large.pxml"))
        finally:
            timeline_views.DECK_MAX_BYTES = old
        self.assertEqual(large.context["deck_refusal"], "too_large")
        self.assertNotIn("deckview.js", large.content.decode())
