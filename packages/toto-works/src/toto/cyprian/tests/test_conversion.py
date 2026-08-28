"""HTML ⇄ CTML: the Edit button, the two conversions, and what each one costs.

The vault's Edit button on an html file USED to dispatch into the writer,
silently minting a twin document and bridging every save back into the page.
It does not any more: Edit opens the HTML, and becoming a CTML document is a
named action that produces a new file. These tests pin both halves — that the
implicit path is gone, and that the explicit one is honest about its losses.
"""

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse

from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin

from toto.cyprian import conversion, ctml as df

from .base import CyprianTestCase

PLAIN = (b'<!doctype html><html><head><meta charset="utf-8">'
         b"<title>Plan</title></head><body><p>before</p></body></html>")
STYLED = (b"<!doctype html><html><head><style>p{color:red}</style></head>"
          b'<body><p class="lede" style="color:red">styled</p>'
          b"<script>x()</script></body></html>")


class ConversionTestCase(CyprianTestCase):
    def _page(self, body=PLAIN, *, owner=None, title="plan.html"):
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type="html",
            bucket=self.bucket, file=SimpleUploadedFile(title, body))

    def _document(self, *, title="plan.ctml", owner=None, document=None):
        document = document or df.new_document("Plan")
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type="ctml",
            bucket=self.bucket,
            file=SimpleUploadedFile(title, df.dumps(document).encode("utf-8")))


class EditOpensTheSourceTests(ConversionTestCase):
    """The implicit dispatch is gone, and stays gone."""

    def test_the_vault_edit_button_opens_the_html_editor(self):
        page = self._page()
        plugin = VaultEditorPlugin.for_file_type("html")
        self.assertEqual(plugin.get_editor_url(page),
                         reverse("editor:html_display", args=[page.pk]))

    def test_there_is_no_edit_html_route_any_more(self):
        from django.urls import NoReverseMatch

        with self.assertRaises(NoReverseMatch):
            reverse("cyprian:edit_html", args=[1])

    def test_a_styled_page_is_not_special_cased(self):
        """It used to fall back to the source editor with a message. Now
        every page goes there, so there is nothing to fall back FROM."""
        page = self._page(STYLED)
        plugin = VaultEditorPlugin.for_file_type("html")
        self.assertEqual(plugin.get_editor_url(page),
                         reverse("editor:html_display", args=[page.pk]))


class HtmlToCtmlTests(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_a_get_shows_the_report_and_writes_nothing(self):
        page = self._page(STYLED)
        before = VaultFile.objects.count()
        response = self.client.get(
            reverse("cyprian:html_to_ctml", args=[page.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(VaultFile.objects.count(), before)

    def test_a_post_creates_a_ctml_file_beside_the_page(self):
        page = self._page()
        response = self.client.post(
            reverse("cyprian:html_to_ctml", args=[page.pk]))
        self.assertEqual(response.status_code, 302)
        made = VaultFile.objects.get(file_type="ctml")
        self.assertEqual(made.title, "plan.ctml")
        self.assertEqual(made.bucket_id, page.bucket_id)
        self.assertEqual(made.directory_id, page.directory_id)

    def test_the_page_is_never_touched(self):
        page = self._page()
        with page.file.open("rb") as handle:
            before = handle.read()
        self.client.post(reverse("cyprian:html_to_ctml", args=[page.pk]))
        page.refresh_from_db()
        with page.file.open("rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(page.file_type, "html")

    def test_converting_twice_makes_a_second_file(self):
        """It used to adopt the first one. That was right while conversion was
        a side effect of pressing Edit; as a deliberate action, silently
        reopening an older file is the surprising answer."""
        page = self._page()
        url = reverse("cyprian:html_to_ctml", args=[page.pk])
        self.client.post(url)
        self.client.post(url)
        titles = set(VaultFile.objects.filter(file_type="ctml")
                     .values_list("title", flat=True))
        self.assertEqual(titles, {"plan.ctml", "plan-2.ctml"})

    def test_a_stranger_is_refused(self):
        page = self._page(owner=self.other)
        self.assertEqual(self.client.post(
            reverse("cyprian:html_to_ctml", args=[page.pk])).status_code, 404)

    def test_a_ctml_file_cannot_be_converted_to_ctml(self):
        document = self._document()
        self.assertEqual(self.client.post(
            reverse("cyprian:html_to_ctml", args=[document.pk])).status_code, 404)


class CtmlToHtmlTests(ConversionTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_a_post_creates_an_html_file_beside_the_document(self):
        document = self._document()
        response = self.client.post(
            reverse("cyprian:ctml_to_html", args=[document.pk]))
        self.assertEqual(response.status_code, 302)
        made = VaultFile.objects.get(file_type="html")
        self.assertEqual(made.title, "plan.html")

    def test_the_document_is_never_touched(self):
        document = self._document()
        with document.file.open("rb") as handle:
            before = handle.read()
        self.client.post(reverse("cyprian:ctml_to_html", args=[document.pk]))
        document.refresh_from_db()
        with document.file.open("rb") as handle:
            self.assertEqual(handle.read(), before)
        self.assertEqual(document.file_type, "ctml")

    def test_the_legacy_spelling_converts_too(self):
        document = self._document()
        VaultFile.objects.filter(pk=document.pk).update(file_type="document")
        self.assertEqual(self.client.post(
            reverse("cyprian:ctml_to_html", args=[document.pk])).status_code, 302)


class ReportTests(SimpleTestCase):
    """The report is counted, not guessed."""

    def test_a_plain_page_loses_nothing_but_its_head(self):
        report = conversion.html_to_ctml(None, PLAIN.decode())
        kinds = {loss.kind for loss in report.losses}
        self.assertEqual(kinds, {"head"})

    def test_every_kind_of_loss_is_named_and_counted(self):
        html = ('<html><head><style>a{}</style>'
                '<link rel="stylesheet" href="a.css"></head><body>'
                '<script>x()</script><script>y()</script>'
                '<form><input></form>'
                '<iframe src="a"></iframe>'
                '<p style="color:red" class="lede" onclick="z()">t</p>'
                "</body></html>")
        report = conversion.html_to_ctml(None, html)
        found = {loss.kind: loss.count for loss in report.losses}
        self.assertEqual(found.get("script"), 2)
        self.assertEqual(found.get("form"), 1)
        self.assertEqual(found.get("frame"), 1)
        self.assertEqual(found.get("inline-style"), 1)
        self.assertEqual(found.get("handler"), 1)
        self.assertEqual(found.get("stylesheet"), 1)
        self.assertGreaterEqual(found.get("class-or-id", 0), 1)
        self.assertFalse(report.lossless)

    def test_the_body_survives(self):
        report = conversion.html_to_ctml(None, PLAIN.decode())
        self.assertIn("before", report.text)

    def test_a_document_with_no_extras_converts_losslessly(self):
        document = df.new_document("Plan")
        document.content = "<h1>Plan</h1><p>Body</p>"
        report = conversion.ctml_to_html(document)
        self.assertTrue(report.lossless)
        self.assertIn("<h1>Plan</h1>", report.text)
        self.assertIn("<title>Plan</title>", report.text)

    def test_a_contents_page_has_nowhere_to_go_in_html(self):
        document = df.new_document("Plan")
        document.toc = True
        report = conversion.ctml_to_html(document)
        self.assertIn("toc", {loss.kind for loss in report.losses})

    def test_the_round_trip_keeps_the_body(self):
        """HTML -> CTML -> HTML. What comes back is the body, faithfully,
        inside a regenerated page — which is what the report promised."""
        source = ("<html><head><title>T</title></head><body>"
                  "<h1>Heading</h1><p>Text with <strong>bold</strong>.</p>"
                  "<ul><li>one</li><li>two</li></ul>"
                  "</body></html>")
        forward = conversion.html_to_ctml(None, source)
        document = df.loads(forward.text)
        back = conversion.ctml_to_html(document).text
        for fragment in ("<h1>Heading</h1>", "<strong>bold</strong>",
                         "<li>one</li>", "<li>two</li>"):
            self.assertIn(fragment, back)


class LosslessnessTests(SimpleTestCase):
    """What `is_compatible` used to answer with a boolean, itemised.

    The predicate is gone. `Report.lossless` is the same question, and having
    one answer instead of two is the point — a boolean that disagreed with the
    list underneath it would be worse than either alone.
    """

    def test_everything_the_writer_would_lose_is_reported(self):
        for html in ("<script>x</script>", "<style>p{}</style>",
                     '<link rel="stylesheet" href="a.css">',
                     '<iframe src="a"></iframe>', "<form></form>",
                     '<p style="color:red">x</p>',
                     '<p onclick="x()">x</p>',
                     '<p class="lede">x</p>', '<p id="top">x</p>'):
            with self.subTest(html=html):
                self.assertFalse(conversion.html_to_ctml(None, html).lossless)

    def test_plain_structure_costs_nothing(self):
        fragment = "<h1>T</h1><table><tr><td>1</td></tr></table>"
        self.assertTrue(conversion.html_to_ctml(None, fragment).lossless)

    def test_a_head_is_the_one_loss_a_whole_page_always_has(self):
        report = conversion.html_to_ctml(None, PLAIN.decode())
        self.assertEqual({loss.kind for loss in report.losses}, {"head"})
