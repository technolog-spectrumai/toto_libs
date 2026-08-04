"""The library, the writer, the reader, saving, and the PDF export."""

import json
from unittest import mock, skipUnless

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin, VaultPlayPlugin

from toto.cyprian import document_format as df, render_pdf

from .base import CyprianTestCase


class DocumentPageTests(CyprianTestCase):
    def _make(self, document=None, *, owner=None, is_public=False,
              title="report.xml", file_type="document") -> VaultFile:
        document = document or df.new_document("Report")
        return VaultFile.objects.create(
            owner=owner or self.owner, title=title, file_type=file_type,
            is_public=is_public, bucket=self.bucket,
            file=SimpleUploadedFile(title, df.dumps(document).encode("utf-8")))

    # -- creating ------------------------------------------------------------
    def test_creating_a_document_drops_into_the_writer(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("cyprian:create"),
                                    {"filename": "quarterly"})
        self.assertEqual(response.status_code, 302)
        vault_file = VaultFile.objects.get(owner=self.owner,
                                           file_type="document")
        self.assertIn(str(vault_file.pk), response["Location"])
        self.assertEqual(vault_file.title, "quarterly.xml")

    def test_creating_needs_a_login(self):
        response = self.client.post(reverse("cyprian:create"), {"filename": "x"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    # -- the writer ----------------------------------------------------------
    def test_the_writer_hydrates_the_document(self):
        vault_file = self._make()
        self.client.force_login(self.owner)
        body = self.client.get(
            reverse("cyprian:edit", args=[vault_file.pk])).content.decode()
        chunk = body.split('id="cy-document"')[1].split("</script>")[0]
        data = json.loads(chunk.split(">", 1)[1])
        self.assertIsInstance(data, dict,
                              "json_script was handed an already-encoded string")
        self.assertEqual(data["title"], "Report")
        self.assertIn("content", data)
        self.assertNotIn("page", data, "paper size is not a choice any more")

    def test_the_writer_loads_the_shared_stylesheet_and_its_modules(self):
        vault_file = self._make()
        self.client.force_login(self.owner)
        body = self.client.get(
            reverse("cyprian:edit", args=[vault_file.pk])).content.decode()
        self.assertIn("cyprian/document.css", body)
        for module in ("cyprian/model.js", "cyprian/editor.js",
                       "cyprian/tiptap_setup.js"):
            self.assertIn(module, body)
        # Reused from memo rather than copied. `drag.js` is NOT among them any
        # more: a section is one document and there is nothing to drag.
        for module in ("memo/history.js", "memo/sanitize.js"):
            self.assertIn(module, body)
        self.assertNotIn("memo/drag.js", body)

    def test_a_stranger_cannot_open_the_writer(self):
        vault_file = self._make()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(
            reverse("cyprian:edit", args=[vault_file.pk])).status_code, 404)

    # -- the reader ----------------------------------------------------------
    def test_a_public_document_reads_for_anyone(self):
        vault_file = self._make(is_public=True)
        response = self.client.get(reverse("cyprian:read", args=[vault_file.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Report")

    def test_a_private_document_sends_an_anonymous_reader_to_login(self):
        vault_file = self._make(is_public=False)
        response = self.client.get(reverse("cyprian:read", args=[vault_file.pk]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_private_document_is_forbidden_to_a_stranger(self):
        vault_file = self._make(is_public=False)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(
            reverse("cyprian:read", args=[vault_file.pk])).status_code, 403)

    def test_something_that_is_not_a_document_is_a_404(self):
        vault_file = VaultFile.objects.create(
            owner=self.owner, title="data.xml", file_type="xml",
            bucket=self.bucket,
            file=SimpleUploadedFile("data.xml", b"<rows><row/></rows>"))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(
            reverse("cyprian:read", args=[vault_file.pk])).status_code, 404)

    def test_the_whole_vocabulary_renders_in_the_reader(self):
        document = df.Document(title="All", content=(
                "<h1>Everything</h1>"
                "<p>prose here</p><ul><li>alpha</li></ul>"
                "<table><tr><th>Head</th></tr></table>"
                "<blockquote><p>quoted</p></blockquote>"
                "<pre><code>print(1)</code></pre>"
                '<div class="cy-callout" data-tone="tip"><p>noted</p></div>'
                '<div class="cy-formula" data-latex="E = mc^2">'
                '<code class="cy-formula-source">E = mc^2</code></div>'
                "<hr>"))
        vault_file = self._make(document, is_public=True)
        body = self.client.get(
            reverse("cyprian:read", args=[vault_file.pk])).content.decode()
        for expected in ("prose here", "<li>alpha</li>", "<th>Head</th>",
                         "quoted", "print(1)", "noted", "E = mc^2",
                         'class="cy-callout"', "<hr>"):
            self.assertIn(expected, body, expected)

    def test_a_version_one_document_reads_the_same_after_conversion(self):
        # The whole point of converting on open rather than refusing: an older
        # file opens, reads identically, and only changes shape when saved.
        v1 = ('<?xml version="1.0" encoding="utf-8"?>\n'
              '<document version="1" title="Old">\n'
              '  <section id="s-1" level="1">\n'
              "    <heading><![CDATA[Legacy]]></heading>\n"
              '    <block id="b-1" type="text"><![CDATA[<p>still here</p>]]></block>\n'
              '    <block id="b-2" type="list"><item><![CDATA[kept]]></item></block>\n'
              "  </section>\n</document>\n")
        vault_file = VaultFile.objects.create(
            owner=self.owner, title="old.xml", file_type="document",
            is_public=True, bucket=self.bucket,
            file=SimpleUploadedFile("old.xml", v1.encode("utf-8")))
        body = self.client.get(
            reverse("cyprian:read", args=[vault_file.pk])).content.decode()
        self.assertIn("Legacy", body)
        self.assertIn("still here", body)
        self.assertIn("<li>kept</li>", body)

    # -- the library ---------------------------------------------------------
    def test_the_library_lists_a_document_with_its_stats(self):
        self._make(df.Document(title="Listed", content="<p>one two three</p>"),
                   is_public=True)
        self.client.force_login(self.owner)
        response = self.client.get(reverse("cyprian:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Listed")

    def test_the_library_renders_other_peoples_documents_safely(self):
        """It lists everybody's documents, so this is the page that matters.

        There is no `trust_html` switch any more: content is sanitised on the
        way in AND on the way out, so a hostile file has nothing live left in it
        by the time any surface sees it.
        """
        self._make(df.Document(title="Hostile",
                               content='<p onmouseover="SENTINEL()">x</p>'
                                       "<script>SENTINEL()</script>"),
                   owner=self.other, is_public=True)
        self.client.force_login(self.owner)
        body = self.client.get(reverse("cyprian:index")).content.decode()
        self.assertNotIn("SENTINEL", body)

    # -- saving --------------------------------------------------------------
    def _post(self, vault_file, payload):
        return self.client.post(reverse("cyprian:save", args=[vault_file.pk]),
                                data=json.dumps(payload),
                                content_type="application/json")

    def test_saving_writes_the_document_back(self):
        vault_file = self._make()
        self.client.force_login(self.owner)
        response = self._post(vault_file, {"document": {
            "title": "Renamed", "content": "<h1>New</h1><p>body</p>"}})
        self.assertEqual(response.status_code, 200)
        vault_file.refresh_from_db()
        saved = df.loads(vault_file.file.read().decode("utf-8"))
        self.assertEqual(saved.title, "Renamed")
        self.assertIn("<h1>New</h1>", saved.content)

    def test_a_document_bigger_than_djangos_form_limit_still_saves(self):
        """`request.body` is capped at DATA_UPLOAD_MAX_MEMORY_SIZE.

        No host sets it, so it defaults to 2.5 MB — a document with a handful of
        embedded images is past that, and autosave would make the failure
        constant rather than rare. The view reads with `request.read()`.
        """
        vault_file = self._make()
        self.client.force_login(self.owner)
        picture = "data:image/png;base64," + ("A" * 4_000_000)
        response = self._post(vault_file, {"document": {
            "title": "Heavy", "content": f'<p>x</p><img src="{picture}" alt="">'}})
        self.assertEqual(response.status_code, 200)
        vault_file.refresh_from_db()
        self.assertGreater(vault_file.file_size_bytes, 4_000_000)

    def test_a_stale_base_hash_is_refused(self):
        vault_file = self._make()
        vault_file.content_hash = vault_file.create_hash()
        vault_file.save(update_fields=["content_hash"])
        self.client.force_login(self.owner)
        response = self._post(vault_file, {"base_hash": "not-current",
                                           "document": {"title": "T", "sections": []}})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["content_hash"], vault_file.content_hash)

    def test_saving_is_owner_only(self):
        vault_file = self._make()
        self.client.force_login(self.other)
        self.assertEqual(
            self._post(vault_file, {"document": {"title": "x"}}).status_code, 404)

    def test_saving_refuses_a_get(self):
        vault_file = self._make()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(
            reverse("cyprian:save", args=[vault_file.pk])).status_code, 405)

    # -- vault routing -------------------------------------------------------
    def test_the_vault_buttons_open_cyprian(self):
        """A plugin only fires when its `key` equals a file_type.

        `xml` belongs to toto.editor, so a document needed its own type to get a
        vault button at all — the exact hole memo had.
        """
        editor = VaultEditorPlugin.for_file_type("document")
        player = VaultPlayPlugin.for_file_type("document")
        self.assertIsNotNone(editor)
        self.assertIsNotNone(player)
        vault_file = self._make()
        self.assertEqual(editor.get_editor_url(vault_file),
                         reverse("cyprian:edit", args=[vault_file.pk]))
        self.assertEqual(player.get_play_url(vault_file),
                         reverse("cyprian:read", args=[vault_file.pk]))

    def test_a_plain_xml_file_still_belongs_to_the_generic_editor(self):
        self.assertEqual(VaultEditorPlugin.for_file_type("xml").get_key(), "xml")
        self.assertIsNone(VaultPlayPlugin.for_file_type("xml"))

    def test_a_document_filed_as_xml_is_adopted_on_open(self):
        vault_file = self._make(file_type="document")
        VaultFile.objects.filter(pk=vault_file.pk).update(file_type="xml")
        self.client.force_login(self.owner)
        self.client.get(reverse("cyprian:edit", args=[vault_file.pk]))
        vault_file.refresh_from_db()
        self.assertEqual(vault_file.file_type, "document")


class SourceViewTests(CyprianTestCase):
    """The Source tab: the document as the file holds it, and back again."""

    def setUp(self):
        super().setUp()
        document = df.new_document("Sourced")
        document.content = "<h1>Chapter one</h1><p>body</p>"
        self.file = VaultFile.objects.create(
            owner=self.owner, title="sourced.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("sourced.xml",
                                    df.dumps(document).encode("utf-8")))
        self.url = reverse("cyprian:source", args=[self.file.pk])
        self.client.force_login(self.owner)

    def test_get_returns_the_file_verbatim(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("<document", body)
        self.assertIn("Chapter one", body)

    def test_post_parses_with_the_servers_own_parser(self):
        document = df.new_document("Edited by hand")
        document.content = "<h1>Rewritten</h1>"
        response = self.client.post(self.url, data=df.dumps(document),
                                    content_type="application/xml")
        self.assertEqual(response.status_code, 200)
        parsed = response.json()["document"]
        self.assertEqual(parsed["title"], "Edited by hand")
        self.assertIn("<h1>Rewritten</h1>", parsed["content"])

    def test_applying_does_not_write_the_file(self):
        # The editor applies it as an ordinary edit, so it goes through undo and
        # autosave. A bad paste can be undone rather than being already on disk.
        before = self.file.file.read()
        document = df.new_document("Not saved")
        self.client.post(self.url, data=df.dumps(document),
                         content_type="application/xml")
        self.file.refresh_from_db()
        self.assertEqual(self.file.file.read(), before)

    def test_rubbish_is_refused_with_the_parsers_sentence(self):
        response = self.client.post(self.url, data="<document><oops",
                                    content_type="application/xml")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["error"])

    def test_a_stranger_cannot_read_the_source(self):
        self.client.force_login(self.other)
        self.assertIn(self.client.get(self.url).status_code, (403, 404))

    def test_the_writer_ships_the_source_url(self):
        body = self.client.get(
            reverse("cyprian:edit", args=[self.file.pk])).content.decode()
        self.assertIn(reverse("cyprian:source", args=[self.file.pk]), body)


class WriterChromeTests(CyprianTestCase):
    """What the writer offers, and what it deliberately no longer offers."""

    def setUp(self):
        super().setUp()
        self.file = VaultFile.objects.create(
            owner=self.owner, title="chrome.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("chrome.xml",
                                    df.dumps(df.new_document("Chrome")).encode()))
        self.client.force_login(self.owner)
        self.body = self.client.get(
            reverse("cyprian:edit", args=[self.file.pk])).content.decode()

    def test_prose_is_edited_in_tiptap(self):
        # An import map and one module: the bare specifiers inside the vendored
        # files resolve to our own static URLs. See tiptap.py.
        self.assertIn('type="importmap"', self.body)
        self.assertIn("@tiptap/core", self.body)
        self.assertIn("cyprian/tiptap_setup.js", self.body)
        self.assertIn('type="module"', self.body)
        self.assertNotIn("trix", self.body)

    def test_the_import_map_comes_before_the_module(self):
        # A map that arrives after the first module script is a map the browser
        # ignores, and every bare specifier then 404s.
        self.assertLess(self.body.index('type="importmap"'),
                        self.body.index("tiptap_setup.js"))

    def test_the_toolbar_is_ours(self):
        for needle in ("cy-toolbar", "toggleHeading(", "toggleBulletList(",
                       "setInk(", "setSize(", "insertTable(",
                       "openDialog('image')", "openDialog('formula')",
                       "setCallout(", "addPageBreak("):
            with self.subTest(needle=needle):
                self.assertIn(needle, self.body)

    def test_table_controls_only_appear_inside_a_table(self):
        # Eight buttons that do nothing most of the time is the clutter this
        # editor exists to avoid.
        self.assertIn('x-if="inTable"', self.body)

    def test_the_source_view_loads_ace(self):
        self.assertIn("vendor/ace/ace.js", self.body)
        self.assertIn('id="cy-source"', self.body)

    def test_there_are_no_sections_to_add(self):
        # One editor for the whole document. A heading in the body is the
        # structure, and it is one keystroke rather than a button, a level and
        # an ordering to manage.
        for gone in ("cy-add-section", "addSection(", "cy-section-bar",
                     "setSectionLevel(", "removeSection("):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.body)
        self.assertIn('id="cy-editor"', self.body)

    def test_paper_size_and_section_numbering_are_gone(self):
        self.assertNotIn("setPage(", self.body)
        self.assertNotIn("toggleNumbering(", self.body)
        self.assertNotIn("Letter", self.body)

    def test_renditions_can_be_saved_into_the_vault(self):
        self.assertIn("saveRendition('pdf')", self.body)
        self.assertIn("saveRendition('html')", self.body)
        # And the link comes back in a modal rather than a flash message you
        # then have to go hunting behind.
        self.assertIn("ui.rendition.url", self.body)

    def test_saving_asks_for_a_name_and_offers_a_watermark(self):
        # The save dialog is the ONE place a file name is asked for — there is
        # no name field in the header — and the name is obligatory.
        self.assertIn("ui.saveDialog.name", self.body)
        self.assertIn("ui.saveDialog.watermark", self.body)
        self.assertNotIn('x-model="state.title"', self.body)

    def test_the_page_boundary_is_drawn_by_the_paginator(self):
        # The canvas stays one continuous element — splitting it into real page
        # elements would move nodes under a live ProseMirror view and lose the
        # caret — so the break is drawn from the measurement.
        # The paginator is a ProseMirror DECORATION now, inside the module:
        # the view owns its DOM and wipes anything written onto it from
        # outside, which is what the first attempt learned the hard way.
        self.assertNotIn("cyprian/paginate.js", self.body)
        self.assertIn("cy-page", self.body)

    def test_the_block_model_is_gone_from_the_page(self):
        # A section is one document now. Nothing on this page knows what a
        # block was. (addParagraph is NOT in this list: the name came back in
        # the toolbar as the paragraph-break tool, which ends the block the
        # caret is in — a writing act, not a block-model resurrection.)
        for gone in ("cy-block", "openBlockDialog",
                     "isInlineEdited", "data-drag-handle"):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, self.body)

    def test_the_canvas_follows_the_platform_theme(self):
        self.assertIn("cy-screen", self.body)
        self.assertIn(":data-theme", self.body)


class ExportTests(CyprianTestCase):
    def setUp(self):
        super().setUp()
        self.vault_file = VaultFile.objects.create(
            owner=self.owner, title="report.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("report.xml", df.dumps(df.Document(
                title="Report", content="<h1>H</h1><p>body</p>")).encode()))
        self.client.force_login(self.owner)
        self.url = reverse("cyprian:export_pdf", args=[self.vault_file.pk])

    def test_without_weasyprint_it_says_what_to_do(self):
        with mock.patch.object(render_pdf, "render",
                               side_effect=render_pdf.PdfUnavailable(
                                   "needs WeasyPrint … BUILD_WEASYPRINT=1")):
            response = self.client.get(self.url)
        self.assertEqual(response.status_code, 503)
        self.assertIn("BUILD_WEASYPRINT", response.content.decode())

    def test_a_stranger_cannot_export_your_document(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_an_html_rendition_takes_the_asked_name_but_not_the_asked_extension(self):
        # The name is the writer's; the extension is OURS — the file IS html,
        # and honouring `report.exe` would be labelling bytes wrongly on the
        # writer's own instruction.
        url = reverse("cyprian:save_html", args=[self.vault_file.pk])
        response = self.client.post(url, {"name": "Quarterly Report.exe"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "quarterly-report.html")

    def test_an_empty_name_falls_back_to_the_documents_own(self):
        url = reverse("cyprian:save_html", args=[self.vault_file.pk])
        response = self.client.post(url, {"name": "   "})
        self.assertEqual(response.json()["name"], "report.html")

    def test_an_html_rendition_lands_in_the_asked_folder(self):
        # The export modal chooses a destination through the same vault gate
        # the New Document flow uses — and someone else's bucket 404s.
        from toto.vault.models import Bucket, VaultDirectory
        other_bucket = Bucket.objects.create(
            slug="dest", name="Dest", owner=self.owner, storage_backend="local")
        folder = VaultDirectory.objects.create(
            name="exports", bucket=other_bucket, owner=self.owner)
        url = reverse("cyprian:save_html", args=[self.vault_file.pk])
        response = self.client.post(url, {
            "name": "filed", "bucket": other_bucket.pk, "directory": folder.pk})
        self.assertEqual(response.status_code, 200)
        saved = VaultFile.objects.get(title="filed.html")
        self.assertEqual(saved.bucket, other_bucket)
        self.assertEqual(saved.directory, folder)

    def test_someone_elses_bucket_is_refused(self):
        from toto.vault.models import Bucket
        theirs = Bucket.objects.create(
            slug="theirs", name="Theirs", owner=self.other, storage_backend="local")
        url = reverse("cyprian:save_html", args=[self.vault_file.pk])
        response = self.client.post(url, {"name": "x", "bucket": theirs.pk})
        self.assertEqual(response.status_code, 404)

    def test_an_image_watermark_reaches_the_print_page_and_junk_does_not(self):
        from django.template.loader import render_to_string

        from toto.cyprian import views as cyprian_views

        good = "data:image/png;base64,iVBORw0K"
        html = render_to_string("cyprian/print.html", {
            "document": df.Document(title="R", content="<p>b</p>"),
            "watermark_image": good, "document_css": "", "katex_css": "",
        })
        self.assertIn('<div class="cy-watermark-image">', html)
        self.assertIn(good, html)

        # The view drops anything that is not a data:image URI — a URL here
        # would have WeasyPrint fetching the network on the writer's behalf.
        class Req:
            POST = {"watermark_image": "https://evil.example/x.png",
                    "watermark": ""}
        text, image = cyprian_views._asked_watermark(Req())
        self.assertEqual(image, "")

    def test_the_watermark_reaches_every_print_page(self):
        # position:fixed repeats on every page in WeasyPrint — that is the whole
        # mechanism — and the text is autoescaped: a watermark is a stamp, not
        # markup.
        from django.template.loader import render_to_string

        document = df.Document(title="R", content="<p>b</p>")
        html = render_to_string("cyprian/print.html", {
            "document": document, "watermark": "DRAFT <b>2026</b>",
            "document_css": "", "katex_css": "",
        })
        self.assertIn('<div class="cy-watermark">', html)
        self.assertIn("DRAFT &lt;b&gt;2026&lt;/b&gt;", html)

    def test_no_watermark_no_stamp_element(self):
        from django.template.loader import render_to_string

        html = render_to_string("cyprian/print.html", {
            "document": df.Document(title="R", content="<p>b</p>"),
            "document_css": "", "katex_css": "",
        })
        # The stylesheet always carries the rule; only the ELEMENT is
        # conditional.
        self.assertNotIn('<div class="cy-watermark">', html)

    def test_the_print_document_carries_the_page_furniture(self):
        """Nothing else in this repo produces any of this.

        A running header, page numbers and a contents page whose numbers are
        resolved after layout are what make an export read as finished.
        """
        from django.template.loader import render_to_string

        document = df.Document(title="Report", toc=True,
                               content="<h1>Introduction</h1><p>body</p>")
        html = render_to_string("cyprian/print.html", {
            "document": document,
            "document_css": render_pdf._page_rule(document),
            "katex_css": "",
        })
        self.assertIn("@page", html)
        self.assertIn("counter(page)", html)
        self.assertIn("target-counter", html)
        self.assertIn("string-set: cy-doc-title", html)
        self.assertIn('href="#h-1"', html, "the TOC must link to a heading")

    def test_the_page_rule_is_a4_with_the_documents_margins(self):
        rule = render_pdf._page_rule(df.Document(margins="narrow"))
        self.assertIn("size: A4", rule)
        self.assertIn("margin: 12mm 12mm", rule)
        self.assertNotIn("Letter", rule)

    def test_the_pdf_degrades_when_katex_was_never_vendored(self):
        with mock.patch("django.contrib.staticfiles.finders.find", return_value=None):
            css, base = render_pdf._katex_assets()
        self.assertEqual(css, "")
        self.assertIsNone(base)

    @skipUnless(render_pdf.is_available(), "WeasyPrint is not installed here")
    def test_the_pdf_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))


@skipUnless(apps.is_installed("toto.notarius"),
            "toto.notarius is a zenobia host app — delta has no contracts")
class ContractIntegrationTests(CyprianTestCase):
    """toto.notarius owns the contract; cyprian owns its prose.

    The link is one meta field in the document, so it round-trips through the
    format for free — it survives a download, a hand edit and a restore.
    The skip is the same fact the views express with `apps.is_installed`
    guards: cyprian must run on a host with no contracts at all.
    """

    def _contract(self, body="Hello **world**."):
        from toto.notarius import contract_format as cf

        contract = cf.Contract(title="Supply agreement")
        contract.content = cf.Content(id="content-1", media_type="text/markdown",
                                      encoding="text", data=body)
        return VaultFile.objects.create(
            owner=self.owner, title="deal.contract", file_type="contract",
            bucket=self.bucket,
            file=SimpleUploadedFile("deal.contract",
                                    cf.dumps(contract).encode("utf-8")))

    def test_opening_a_contract_creates_a_companion_document(self):
        contract_file = self._contract()
        self.client.force_login(self.owner)
        response = self.client.get(
            reverse("cyprian:from_contract", args=[contract_file.pk]))
        self.assertEqual(response.status_code, 302)

        companion = VaultFile.objects.get(file_type="document",
                                          bucket=self.bucket)
        self.assertIn(str(companion.pk), response["Location"])
        document = df.loads(companion.file.read().decode("utf-8"))
        # The Markdown came through the renderer notarius already used, so the
        # writer shows what the contract PDF was showing.
        self.assertIn("<strong>world</strong>", document.content)
        self.assertEqual(document.meta["contract"], str(contract_file.pk))

    def test_opening_it_twice_reuses_the_same_document(self):
        contract_file = self._contract()
        self.client.force_login(self.owner)
        first = self.client.get(reverse("cyprian:from_contract",
                                        args=[contract_file.pk]))
        second = self.client.get(reverse("cyprian:from_contract",
                                         args=[contract_file.pk]))
        self.assertEqual(first["Location"], second["Location"])
        self.assertEqual(
            VaultFile.objects.filter(file_type="document").count(), 1)

    def test_saving_writes_the_body_back_into_the_contract(self):
        from toto.notarius import contract_format as cf

        contract_file = self._contract()
        self.client.force_login(self.owner)
        self.client.get(reverse("cyprian:from_contract", args=[contract_file.pk]))
        companion = VaultFile.objects.get(file_type="document")

        self.client.post(
            reverse("cyprian:save", args=[companion.pk]),
            data=json.dumps({"document": {
                "title": "Supply agreement",
                "content": "<p>Rewritten in the writer.</p>",
                "meta": {"contract": str(contract_file.pk)}}}),
            content_type="application/json")

        contract_file.refresh_from_db()
        contract = cf.loads(contract_file.file.read().decode("utf-8"))
        self.assertEqual(contract.content.media_type, "text/html")
        self.assertIn("Rewritten in the writer", contract.content.data)

    def test_a_stale_link_does_not_stop_the_document_saving(self):
        # A document that outlived its contract is still a document; refusing
        # to save it would be losing work over a broken pointer.
        vault_file = VaultFile.objects.create(
            owner=self.owner, title="orphan.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("orphan.xml", df.dumps(
                df.Document(title="Orphan", content="<p>x</p>")).encode()))
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("cyprian:save", args=[vault_file.pk]),
            data=json.dumps({"document": {
                "title": "Orphan", "content": "<p>still saves</p>",
                "meta": {"contract": "999999"}}}),
            content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_a_contract_body_written_as_html_renders_in_notarius(self):
        from toto.notarius import contract_format as cf, render

        contract = cf.Contract(title="T")
        contract.content = cf.Content(
            id="content-1", media_type="text/html", encoding="text",
            data='<p>Prose <strong>from the writer</strong>.</p>'
                 "<script>alert(1)</script>")
        html = render._body_html(contract)
        self.assertIn("<strong>from the writer</strong>", html)
        # Sanitised there AND here: a `.contract` can arrive by upload.
        self.assertNotIn("alert(1)", html)
