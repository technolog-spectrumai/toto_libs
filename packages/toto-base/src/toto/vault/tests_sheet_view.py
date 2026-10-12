"""The read-only sheet viewer (2026-10-12): the Play button of a CSV file and
of a ``.uson`` sheet, and the page behind it. The grid is Univer's and is
drawn in the browser (``sheetview/``); here: who gets the page, that the text
reaches it as data, what is refused, and that the page has no way to write.

    manage.py test toto.vault.tests_sheet_view
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

CSV = "name;qty\r\n<b>Ann</b>;12\r\n"


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="sheet-view-"),
                   VAULT_ROOT=tempfile.mkdtemp(prefix="sheet-view-vault-"))
class SheetViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        User = get_user_model()
        cls.ada = User.objects.create_user("ada", password="pw")
        cls.eve = User.objects.create_user("eve", password="pw")
        cls.bucket = Bucket.objects.create(name="Ada", slug="ada", owner=cls.ada)

    def make(self, title="list.csv", content=CSV, file_type="csv", **more):
        vault_file = VaultFile(title=title, owner=self.ada, bucket=self.bucket,
                               file_type=file_type, **more)
        vault_file.file.save(title, ContentFile(content.encode()), save=True)
        return vault_file

    def page(self, user, vault_file):
        self.client.force_login(user)
        return self.client.get(reverse("vault:sheet_view", args=[vault_file.pk]))

    def test_csv_and_uson_have_a_play_plugin_that_leads_to_the_page(self):
        note = self.make()
        for file_type in ("csv", "uson"):
            plugin = VaultPlayPlugin.for_file_type(file_type)
            self.assertIsNotNone(plugin, file_type)
            self.assertEqual(plugin.get_play_url(note), reverse("vault:sheet_view", args=[note.pk]))

    def test_the_bundle_and_its_stylesheet_are_shipped(self):
        for name in ("vault/sheetview/sheetview.js", "vault/sheetview/sheetview.css"):
            self.assertIsNotNone(finders.find(name), name)

    def test_the_tables_text_reaches_the_page_as_data_and_the_page_cannot_write(self):
        response = self.page(self.ada, self.make())
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.context["sheet_kind"], response.context["sheet_source"]), ("csv", CSV))
        body = response.content.decode()
        self.assertIn('id="sheet-source" type="application/json"', body)
        self.assertIn('data-kind="csv"', body)
        self.assertNotIn("<b>Ann</b>", body)                  # escaped inside the data
        self.assertIn("Read only", body)
        article = body[body.index('data-testid="sheet-view"'):body.index("</article>")]
        for way_to_write in ("<form", 'method="post"', "fetch(", "XMLHttpRequest"):
            self.assertNotIn(way_to_write, article)
        self.assertEqual(self.client.post(reverse("vault:sheet_view", args=[1])).status_code, 405)

    def test_a_uson_sheet_opens_as_one(self):
        sheet = self.make("plan.uson", '{"format": "uson", "version": 1, "workbook": {}}', file_type="uson")
        self.assertEqual(self.page(self.ada, sheet).context["sheet_kind"], "uson")

    def test_who_may_not_read_the_file_and_what_is_no_table(self):
        self.assertEqual(self.page(self.eve, self.make()).status_code, 404)
        self.assertEqual(self.page(self.ada, self.make("note.md", "# hi", file_type="markdown")).status_code, 404)
        self.client.logout()
        self.assertIn(self.client.get(reverse("vault:sheet_view", args=[1])).status_code, (302, 403))

    def test_an_encrypted_table_and_a_large_one_are_not_read(self):
        from toto.vault import timeline_views

        sealed = self.page(self.ada, self.make("sealed.csv", is_encrypted=True))
        self.assertEqual((sealed.context["sheet_refusal"], sealed.context["sheet_source"]), ("encrypted", ""))
        old, timeline_views.SHEET_MAX_BYTES = timeline_views.SHEET_MAX_BYTES, 5
        try:
            large = self.page(self.ada, self.make("large.csv"))
        finally:
            timeline_views.SHEET_MAX_BYTES = old
        self.assertEqual(large.context["sheet_refusal"], "too_large")
        self.assertNotIn("sheetview.js", large.content.decode())
