"""Cyprian as the default html editor — for pages it cannot damage.

On hosts that install cyprian, the vault's Edit button on an html file
dispatches to `cyprian:edit_html`. A COMPATIBLE page (nothing the writer
would silently lose — see `from_html.is_compatible`) opens in the writer
via a twin document whose bridge writes every save back into the page;
anything else falls back to the source editor with the reason said out
loud. The html file stays the single source of truth throughout.
"""

from unittest import skipUnless

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin

from toto.cyprian import document_format as df
from toto.cyprian import from_html
from toto.cyprian.bridge import DocumentBridge, write_back

from .base import CyprianTestCase

PLAIN = (b'<!doctype html><html><head><meta charset="utf-8">'
         b"<title>Plan</title></head><body><p>before</p></body></html>")
STYLED = (b"<!doctype html><html><head><style>p{color:red}</style></head>"
          b"<body><p>styled</p></body></html>")


class EditHtmlTestCase(CyprianTestCase):
    def _page(self, body=PLAIN, *, owner=None, title="plan.html"):
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type="html",
            bucket=self.bucket, file=SimpleUploadedFile(title, body))

    def _twin(self):
        return VaultFile.objects.get(file_type="document")


@skipUnless(apps.is_installed("toto.editor"),
            "the dispatch's fallback is the source editor")
class DispatchTests(EditHtmlTestCase):
    def test_the_vault_edit_button_lands_on_the_dispatch(self):
        page = self._page()
        plugin = VaultEditorPlugin.for_file_type("html")
        self.assertEqual(plugin.get_editor_url(page),
                         reverse("cyprian:edit_html", args=[page.pk]))

    def test_a_compatible_page_opens_in_the_writer(self):
        page = self._page()
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("cyprian:edit_html", args=[page.pk]))
        twin = self._twin()
        self.assertEqual(response["Location"],
                         reverse("cyprian:edit", args=[twin.pk]))

    def test_the_dispatch_twice_reuses_the_twin(self):
        """`convert` adopts the same-named document — no forked work."""
        page = self._page()
        self.client.force_login(self.owner)
        self.client.get(reverse("cyprian:edit_html", args=[page.pk]))
        self.client.get(reverse("cyprian:edit_html", args=[page.pk]))
        self.assertEqual(
            VaultFile.objects.filter(file_type="document").count(), 1)

    def test_a_styled_page_falls_back_to_the_source_editor(self):
        """The one loss the writer would inflict, refused up front."""
        page = self._page(STYLED)
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("cyprian:edit_html", args=[page.pk]))
        self.assertEqual(response["Location"],
                         reverse("editor:html_display", args=[page.pk]))
        self.assertFalse(
            VaultFile.objects.filter(file_type="document").exists())

    def test_a_stranger_is_refused_the_dispatch(self):
        page = self._page()
        self.client.force_login(self.other)
        response = self.client.get(
            reverse("cyprian:edit_html", args=[page.pk]))
        self.assertEqual(response.status_code, 404)


class CompatibilityTests(SimpleTestCase):
    def test_everything_the_writer_would_lose_is_incompatible(self):
        for html in ("<script>x</script>", "<style>p{}</style>",
                     '<link rel="stylesheet" href="a.css">',
                     '<iframe src="a"></iframe>', "<form></form>",
                     '<p style="color:red">x</p>',
                     '<p onclick="x()">x</p>',
                     '<p class="lede">x</p>', '<p id="top">x</p>'):
            with self.subTest(html=html):
                self.assertFalse(from_html.is_compatible(html))

    def test_plain_structure_is_compatible(self):
        self.assertTrue(from_html.is_compatible(PLAIN.decode()))
        self.assertTrue(from_html.is_compatible(
            "<h1>T</h1><table><tr><td>1</td></tr></table>"))


class WriteBackTests(EditHtmlTestCase):
    def _twin_document(self):
        twin = self._twin()
        with twin.file.open("rb") as handle:
            return twin, df.loads(handle.read().decode("utf-8"))

    def _page_bytes(self, page):
        page.refresh_from_db()
        with page.file.open("rb") as handle:
            return handle.read()

    def test_saving_the_twin_writes_the_page_back(self):
        page = self._page()
        self.client.force_login(self.owner)
        self.client.get(reverse("cyprian:edit_html", args=[page.pk]))
        twin, document = self._twin_document()
        document.content = "<p>after the writer</p>"
        write_back(twin, document, user=self.owner)
        html = self._page_bytes(page).decode("utf-8")
        self.assertIn("after the writer", html)
        # What came back is still a page the writer may reopen.
        self.assertTrue(from_html.is_compatible(html))

    def test_a_strangers_save_does_not_touch_the_page(self):
        """`can_edit` opens for the page's owner alone."""
        page = self._page()
        self.client.force_login(self.owner)
        self.client.get(reverse("cyprian:edit_html", args=[page.pk]))
        twin, document = self._twin_document()
        document.content = "<p>vandalism</p>"
        write_back(twin, document, user=self.other)
        self.assertNotIn(b"vandalism", self._page_bytes(page))

    def test_a_forged_ref_cannot_aim_at_a_strangers_page(self):
        """Meta is writable by any saver; the bridge must not trust it."""
        theirs = self._page(owner=self.other, title="theirs.html")
        before = self._page_bytes(theirs)
        document = df.new_document("Mine")
        document.meta = {"htmlview.source": str(theirs.pk)}
        mine = VaultFile.objects.create(
            owner=self.owner, title="mine.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("mine.xml",
                                    df.dumps(document).encode("utf-8")))
        self.assertIsNone(DocumentBridge.for_file(mine, document))
        write_back(mine, document, user=self.owner)
        self.assertEqual(self._page_bytes(theirs), before)
