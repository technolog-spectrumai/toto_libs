"""The library, the writer, the reader, saving, and the PDF export."""

import json
from unittest import mock, skipUnless

from django.apps import apps
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin, VaultPlayPlugin


from toto.cyprian import document_format as df

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
        for module in ("cyprian/history.js", "antivirus/sanitize.js"):
            self.assertIn(module, body)
        self.assertNotIn("cyprian/drag.js", body)

    def test_a_stranger_cannot_open_the_writer(self):
        vault_file = self._make()
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(
            reverse("cyprian:edit", args=[vault_file.pk])).status_code, 404)

    # -- the reader ----------------------------------------------------------


    # -- the library ---------------------------------------------------------


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

    def test_the_lock_banner_is_up_by_the_toolbar_not_buried_in_the_panel(self):
        """The message a locked-out writer needs must be where they are looking.

        It used to render only inside oya/_file_versions.html, which every
        editor puts in a `showVersions` panel that starts collapsed at the foot
        of the page — so the person whose save just 423'd saw nothing to explain
        it. Pinned by position: the banner must come before the versions panel.
        """
        self.assertIn("vault-lock", self.body)          # the event it listens for
        self.assertIn("is editing this right now", self.body)
        self.assertLess(self.body.index("vault-lock"),
                        self.body.index("showVersions"))

    def test_the_page_claims_the_lock_exactly_once(self):
        """The banner listens; it must not instantiate a second fileVersions.

        Two instances would claim the lock twice and run two heartbeats against
        the same file — which is why the state is published as an event rather
        than read from a second component.
        """
        self.assertEqual(self.body.count("fileVersions("), 1)

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

        contract = cf.Contract(title="Supply agreement")
        contract.content = cf.Content(id="content-1", media_type="text/markdown",
                                      encoding="text", data=body)
        return VaultFile.objects.create(
            owner=self.owner, title="deal.contract", file_type="contract",
            bucket=self.bucket,
            file=SimpleUploadedFile("deal.contract",
                                    cf.dumps(contract).encode("utf-8")))


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


class DeletionTests(CyprianTestCase):
    """Deleting a document is the VAULT's delete, surfaced — not a second one."""

    def setUp(self):
        super().setUp()
        self.vault_file = VaultFile.objects.create(
            owner=self.owner, title="doomed.xml", file_type="document",
            bucket=self.bucket,
            file=SimpleUploadedFile("doomed.xml", df.dumps(
                df.Document(title="Doomed", content="<p>x</p>")).encode()))


    def test_the_writer_offers_delete_through_the_vault(self):
        self.client.force_login(self.owner)
        body = self.client.get(
            reverse("cyprian:edit", args=[self.vault_file.pk])).content.decode()
        self.assertIn("Delete document", body)
        self.assertIn(reverse("vault:delete_file"), body)

    def test_the_vault_endpoint_deletes_a_document_for_its_owner_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(
            reverse("vault:delete_file"), {"file_pk": self.vault_file.pk}).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(
            reverse("vault:delete_file"), {"file_pk": self.vault_file.pk}).status_code, 200)
        self.assertFalse(VaultFile.objects.filter(pk=self.vault_file.pk).exists())
