"""Tests for the file-based presentation format + vault-wired viewer/editor."""

from __future__ import annotations

import base64
import io
import pathlib
import re
import json
import zipfile
import tempfile

from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.memo import bundle, presentation_format as pf, render_pdf, tiptap
from toto.vault.models import Bucket, VaultDirectory, VaultFile
from toto.vault.plugins import VaultEditorPlugin, VaultPlayPlugin

User = get_user_model()


V1_DOC = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<presentation version="1" title="Old talk">\n'
    "  <slide>\n"
    "    <title>Legacy</title>\n"
    '    <body><![CDATA[<div style="display:flex"><p>hand written</p></div>]]></body>\n'
    "  </slide>\n"
    "</presentation>\n"
)


def _deck() -> pf.Presentation:
    """One deck exercising every block type and two layouts."""
    return pf.Presentation(
        title="My Talk", theme="white",
        slides=[
            pf.Slide(id="s-1", title="Welcome", layout="title-content", blocks=[
                pf.Block(id="b-1", type="heading", payload="Where we landed",
                         attrs={"level": "2"}),
                pf.Block(id="b-2", type="text",
                         payload="<p>Hello &amp; <b>world</b></p>"),
                pf.Block(id="b-3", type="list", items=["one", "two"]),
            ]),
            pf.Slide(id="s-2", title="Detail", layout="two-column", blocks=[
                pf.Block(id="b-4", type="image", slot="left", attrs={"alt": "pic"},
                         payload="data:image/png;base64,iVBORw0KGgo="),
                pf.Block(id="b-5", type="svg", slot="right",
                         payload='<svg viewBox="0 0 10 10"><rect/></svg>'),
                pf.Block(id="b-6", type="code", attrs={"language": "python"},
                         payload='print("hi")'),
                pf.Block(id="b-7", type="quote", attrs={"cite": "Ada"},
                         payload="Patterns."),
            ]),
        ],
    )


class PresentationFormatTests(TestCase):
    def test_round_trip_preserves_every_block_type(self):
        back = pf.loads(pf.dumps(_deck()))

        self.assertEqual(back.title, "My Talk")
        self.assertEqual(back.theme, "white")
        self.assertEqual([s.layout for s in back.slides],
                         ["title-content", "two-column"])
        self.assertEqual(
            [b.type for b in back.slides[0].blocks], ["heading", "text", "list"])
        self.assertEqual(back.slides[0].blocks[2].items, ["one", "two"])
        self.assertIn("data:image/png;base64,", back.slides[1].blocks[0].payload)
        self.assertIn("<svg", back.slides[1].blocks[1].payload)
        self.assertEqual(back.slides[1].blocks[0].slot, "left")
        self.assertEqual(back.slides[1].blocks[2].attrs["language"], "python")

    def test_a_cdata_terminator_survives(self):
        p = pf.Presentation(slides=[pf.Slide(id="s-1", blocks=[
            pf.Block(id="b-1", type="text", payload="contains ]]> a terminator")])])
        self.assertEqual(pf.loads(pf.dumps(p)).slides[0].blocks[0].payload,
                         "contains ]]> a terminator")

    def test_dumps_is_stable_from_the_second_serialisation(self):
        # NOT dumps(loads(x)) == x for arbitrary x: parsing backfills missing
        # ids, so the first serialisation of a document that had none is
        # legitimately different. From then on it must never drift.
        once = pf.dumps(_deck())
        self.assertEqual(pf.dumps(pf.loads(once)), once)

    # -- v1 compatibility ------------------------------------------------
    def test_a_v1_slide_becomes_one_html_block(self):
        back = pf.loads(V1_DOC)
        self.assertEqual(back.title, "Old talk")
        self.assertEqual(back.slides[0].title, "Legacy")
        self.assertEqual([b.type for b in back.slides[0].blocks], ["html"])

    def test_a_v1_body_is_preserved_verbatim(self):
        # The escape hatch is deliberately not sanitised: its trust model is
        # exactly what v1's `{{ slide.body|safe }}` already was, and rewriting
        # somebody's hand-written slide would be the worse bargain.
        back = pf.loads(V1_DOC)
        self.assertEqual(back.slides[0].blocks[0].payload,
                         '<div style="display:flex"><p>hand written</p></div>')

    def test_the_body_shim_still_answers(self):
        self.assertEqual(pf.loads(V1_DOC).slides[0].body,
                         '<div style="display:flex"><p>hand written</p></div>')

    def test_v2_is_detected_from_blocks_without_a_version_attribute(self):
        # A hand-edit that added blocks and forgot the attribute must not be
        # silently flattened back into one HTML blob.
        back = pf.loads(
            '<presentation title="t"><slide><title>T</title>'
            '<block type="text"><![CDATA[hi]]></block></slide></presentation>')
        self.assertEqual([b.type for b in back.slides[0].blocks], ["text"])

    # -- the anti-trap contract -------------------------------------------
    def test_unknown_markup_round_trips(self):
        """Anything the parser does not understand is preserved, not dropped.

        v1 read four fields and threw the rest of the tree away, so the format
        could not be extended by hand and a newer document lost data the moment
        an older build saved it.
        """
        doc = (
            '<presentation version="2" title="t" data-x="keep">'
            '<slide id="s-1" layout="section" data-note="keep">'
            "<title>T</title>"
            '<block id="b-1" type="sparkline" wobble="3"><![CDATA[1,2,3]]></block>'
            "<notes>from a future version</notes>"
            "</slide></presentation>"
        )
        out = pf.dumps(pf.loads(doc))
        self.assertIn('data-x="keep"', out, "unknown root attribute dropped")
        self.assertIn('data-note="keep"', out, "unknown slide attribute dropped")
        self.assertIn('type="sparkline"', out, "unknown block type dropped")
        self.assertIn('wobble="3"', out, "unknown block attribute dropped")
        self.assertIn("<notes>", out, "unknown child element dropped")

    # -- identity ---------------------------------------------------------
    def test_ids_are_backfilled(self):
        back = pf.loads(V1_DOC)
        self.assertTrue(back.slides[0].id)
        self.assertTrue(back.slides[0].blocks[0].id)

    def test_duplicate_ids_are_made_unique(self):
        # Ids are the :key for every x-for in the editor. Two rows sharing one
        # makes Alpine reuse the wrong DOM node on a reorder.
        back = pf.loads('<presentation version="2">'
                        '<slide id="x"><title/></slide>'
                        '<slide id="x"><title/></slide></presentation>')
        self.assertNotEqual(back.slides[0].id, back.slides[1].id)

    # -- clamping ---------------------------------------------------------
    def test_an_unknown_layout_falls_back_rather_than_raising(self):
        back = pf.loads('<presentation version="2"><slide layout="hexagonal">'
                        "<title>t</title></slide></presentation>")
        self.assertEqual(back.slides[0].layout, pf.DEFAULT_LAYOUT)

    def test_an_unknown_theme_falls_back(self):
        self.assertEqual(
            pf.loads('<presentation version="2" theme="chartreuse"/>').theme,
            pf.DEFAULT_THEME)

    def test_too_many_slides_are_truncated_not_rejected(self):
        # A corrupt file must still open.
        doc = ('<presentation version="2">'
               + "<slide><title>x</title></slide>" * (pf.MAX_SLIDES + 10)
               + "</presentation>")
        self.assertEqual(len(pf.loads(doc).slides), pf.MAX_SLIDES)

    # -- unchanged behaviour ----------------------------------------------
    def test_empty_input_yields_default(self):
        for raw in ("", "   \n  "):
            self.assertEqual(len(pf.loads(raw).slides), 1)

    def test_invalid_xml_raises(self):
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<presentation><slide>")

    def test_wrong_root_raises(self):
        with self.assertRaises(pf.PresentationParseError):
            pf.loads("<other></other>")

    def test_is_presentation_detects_root(self):
        self.assertTrue(pf.is_presentation(pf.dumps(pf.new_presentation("x"))))
        self.assertTrue(pf.is_presentation(b'<presentation version="1"></presentation>'))
        self.assertFalse(pf.is_presentation("<notebook></notebook>"))
        self.assertFalse(pf.is_presentation(""))

    def test_the_cheap_sniff_agrees_with_the_full_parse(self):
        # The gallery filters a few hundred XML files; a full parse there means
        # parsing every embedded image just to read one tag.
        for raw in (pf.dumps(pf.new_presentation("x")),
                    '<?xml version="1.0"?>\n<presentation version="2">',
                    "<!-- a note --><presentation/>"):
            self.assertTrue(pf.sniff_is_presentation(raw), raw[:40])
        for raw in ('<?xml version="1.0"?><notebook/>', "<other/>", ""):
            self.assertFalse(pf.sniff_is_presentation(raw), raw[:40])

    def test_a_blank_deck_has_one_slide_with_one_text_block(self):
        blank = pf.loads(pf.dumps(pf.new_presentation("New")))
        self.assertEqual(len(blank.slides), 1)
        self.assertEqual([b.type for b in blank.slides[0].blocks], ["text"])
        self.assertEqual(blank.slides[0].layout, pf.DEFAULT_LAYOUT)


class SanitisationTests(TestCase):
    """The server allowlist. The client mirror is for tidiness, not safety."""

    def _block(self, kind, payload="", **attrs):
        deck = pf.Presentation.from_dict({"slides": [{"title": "t", "blocks": [
            {"type": kind, "payload": payload, "attrs": attrs}]}]})
        return deck.slides[0].blocks[0]

    def test_a_script_tag_is_dropped_with_its_contents(self):
        self.assertEqual(
            self._block("text", "<p>ok</p><script>alert(1)</script>").payload,
            "<p>ok</p>")

    def test_event_handlers_are_stripped(self):
        self.assertEqual(self._block("text", '<p onclick="x()">hi</p>').payload,
                         "<p>hi</p>")

    def test_a_disallowed_tag_is_unwrapped_not_dropped(self):
        # Pasting from a word processor must lose the styling, not the words.
        self.assertEqual(self._block("text", "<font><big>words</big></font>").payload,
                         "words")

    def test_the_legacy_block_vocabulary_survives(self):
        # <div> is how the Trix era wrote a line, and every deck saved then
        # still holds them. Unwrapping the legacy tags would not lose words but
        # WOULD reflow every old deck on its next save, which reads as the
        # editor eating your formatting. Attributes still go.
        payload = self._block(
            "text",
            '<div style="color:red">one</div><h1>Title</h1>'
            '<blockquote><div>quoted</div></blockquote><del>gone</del>').payload
        self.assertEqual(
            payload,
            "<div>one</div><h1>Title</h1><blockquote><div>quoted</div>"
            "</blockquote><del>gone</del>")

    def test_javascript_hrefs_are_removed(self):
        for href in ("javascript:alert(1)", "java\tscript:alert(1)",
                     "data:text/html;base64,x"):
            self.assertEqual(self._block("text", f'<a href="{href}">x</a>').payload,
                             "<a>x</a>", href)

    def test_ordinary_links_survive(self):
        for href in ("https://example.com", "mailto:a@b.c", "/decks", "#3"):
            self.assertIn(href, self._block("text", f'<a href="{href}">x</a>').payload)

    def test_a_heading_keeps_only_inline_marks(self):
        self.assertEqual(self._block("heading", "<p>A <b>bold</b> idea</p>").payload,
                         "A <b>bold</b> idea")

    def test_code_is_kept_literal(self):
        """A code block is text, and every surface escapes it on render.

        Sanitising it would drop `<script>alert(1)</script>` **with its
        contents** — silently deleting the exact snippet somebody pasted in to
        show their audience. Safety comes from escaping at render, which is both
        correct and lossless.
        """
        for raw in ("<b>print</b>(1)", "<script>alert(1)</script>", "a < b && c"):
            self.assertEqual(self._block("code", raw).payload, raw)

    def test_an_image_must_be_a_self_contained_data_uri(self):
        # An external src would stop the deck being self-contained and turn
        # every viewer into a tracked hit.
        self.assertEqual(self._block("image", "https://evil.example/x.png").payload, "")
        self.assertTrue(self._block("image", "data:image/png;base64,iVBOR=").payload)

    def test_svg_scripting_vectors_are_stripped(self):
        for markup, banned in (
            ('<svg onload="alert(1)"><rect/></svg>', "onload"),
            ("<svg><script>alert(1)</script></svg>", "script"),
            ("<svg><foreignObject><b>x</b></foreignObject></svg>", "foreignObject"),
            ('<svg><use href="//evil/x#y"/></svg>', "evil"),
        ):
            self.assertNotIn(banned, self._block("svg", markup).payload, markup)

    def test_an_internal_svg_reference_survives(self):
        self.assertIn('href="#local"',
                      self._block("svg", '<svg><use href="#local"/></svg>').payload)

    def test_the_html_escape_hatch_is_left_alone(self):
        # Asserted deliberately so nobody "fixes" it later without deciding to.
        raw = '<div style="display:flex" onclick="x">hi</div>'
        self.assertEqual(self._block("html", raw).payload, raw)

    def test_content_is_sanitised_on_open_not_only_on_save(self):
        # A deck is not only ever written by this editor — anyone can upload an
        # XML file to the vault and open it, and a public one renders in every
        # visitor's gallery.
        deck = pf.loads('<presentation version="2"><slide><title>t</title>'
                        '<block type="svg"><![CDATA[<svg onload="x"><rect/></svg>]]>'
                        "</block></slide></presentation>")
        self.assertNotIn("onload", deck.slides[0].blocks[0].payload)


class SaveEndpointTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.deck = VaultFile.objects.create(
            owner=self.alice, title="talk.xml", file_type="presentation",
            bucket=self.bucket,
            file=SimpleUploadedFile("talk.xml",
                                    pf.dumps(pf.new_presentation("T")).encode()))
        self.deck.content_hash = self.deck.create_hash()
        self.deck.save(update_fields=["content_hash"])
        self.client.force_login(self.alice)
        self.url = reverse("memo:save", args=[self.deck.pk])

    def _post(self, payload):
        return self.client.post(self.url, data=json.dumps(payload),
                                content_type="application/json")

    def test_a_deck_bigger_than_django_s_form_limit_still_saves(self):
        """The regression this endpoint was written for.

        `request.body` is checked against DATA_UPLOAD_MAX_MEMORY_SIZE, which no
        host sets — so it defaults to 2.5 MB and saving a deck with about twenty
        embedded images raised RequestDataTooBig before the view ran. Autosave
        would have made that constant rather than occasional.
        """
        big = "data:image/png;base64," + ("A" * 4 * 1024 * 1024)
        res = self._post({"presentation": {"title": "Big", "slides": [
            {"title": "s", "blocks": [{"type": "image", "payload": big}]}]}})
        self.assertEqual(res.status_code, 200, res.content[:200])
        self.deck.refresh_from_db()
        self.assertGreater(self.deck.file_size_bytes, 4 * 1024 * 1024)

    def test_past_the_explicit_ceiling_it_is_a_413(self):
        with override_settings(MEMO_MAX_DECK_BYTES=1024):
            res = self._post({"presentation": {"title": "x" * 4000, "slides": []}})
        self.assertEqual(res.status_code, 413)

    def test_a_stale_base_hash_is_refused(self):
        # Two tabs, or autosave racing a manual save. Without this the slower
        # writer silently wins and the other's work is gone with no error.
        res = self._post({"base_hash": "not-the-current-one",
                          "presentation": {"title": "T", "slides": []}})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["content_hash"], self.deck.content_hash)

    def test_the_matching_base_hash_goes_through_and_returns_the_new_one(self):
        res = self._post({"base_hash": self.deck.content_hash,
                          "presentation": {"title": "T2", "slides": [{"title": "a"}]}})
        self.assertEqual(res.status_code, 200)
        self.deck.refresh_from_db()
        self.assertEqual(res.json()["content_hash"], self.deck.content_hash)

    def test_saving_without_a_base_hash_still_works(self):
        self.assertEqual(self._post(
            {"presentation": {"title": "T", "slides": [{"title": "a"}]}}).status_code, 200)

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_the_bare_document_shape_is_still_accepted(self):
        # An editor page cached across the deploy posts the document itself
        # rather than {"presentation": ...}.
        res = self._post({"title": "Bare", "slides": [{"title": "a"}]})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(pf.loads(self.deck.file.read().decode()).title, "Bare")


class EditorPageTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.deck = VaultFile.objects.create(
            owner=self.alice, title="talk.xml", file_type="presentation",
            bucket=self.bucket,
            file=SimpleUploadedFile("talk.xml", pf.dumps(_deck()).encode()))
        self.deck.content_hash = self.deck.create_hash()
        self.deck.save(update_fields=["content_hash"])
        self.client.force_login(self.alice)

    def _page(self):
        return self.client.get(
            reverse("memo:edit", args=[self.deck.pk])).content.decode()

    def _island(self, body, element_id):
        chunk = body.split(f'id="{element_id}"')[1].split("</script>")[0]
        value = json.loads(chunk.split(">", 1)[1])
        self.assertIsInstance(value, (dict, list),
                              "json_script was handed an already-encoded string")
        return value

    def test_the_deck_hydrates_as_structured_blocks(self):
        data = self._island(self._page(), "memo-data")
        self.assertEqual(data["theme"], "white")
        self.assertEqual([b["type"] for b in data["slides"][0]["blocks"]],
                         ["heading", "text", "list"])
        self.assertEqual(data["slides"][1]["layout"], "two-column")

    def test_every_block_carries_a_stable_id(self):
        # The :key for every x-for. Without ids Alpine reuses DOM nodes by
        # position, so a reorder leaves the text in the wrong block — which is
        # what makes drag-and-drop and index keys mutually exclusive.
        data = self._island(self._page(), "memo-data")
        ids = [b["id"] for s in data["slides"] for b in s["blocks"]]
        self.assertTrue(all(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_the_config_island_carries_the_hash_and_the_urls(self):
        config = self._island(self._page(), "memo-config")
        self.assertEqual(config["contentHash"], self.deck.content_hash)
        for key in ("save", "embed", "upload"):
            self.assertIn(key, config["urls"])

    def test_the_page_loads_the_shared_slide_stylesheet(self):
        # Not a lookalike: the same file the player and the export use.
        body = self._page()
        self.assertIn("memo/slide.css", body)
        self.assertIn("memo-slide", body)

    def test_the_logic_lives_in_static_files(self):
        body = self._page()
        for module in ("model.js", "history.js", "sanitize.js",
                       "canvas.js", "editor.js"):
            self.assertIn("memo/" + module, body)
        self.assertNotIn("function presentationEditor", body,
                         "inline component logic is back")

    def test_nothing_on_a_slide_is_dragged(self):
        # The layout owns the geometry. Dragging was the only way to end up
        # with a deck whose boxes disagreed with its layout, and every drag it
        # offered has a button now: box toolbars for content, the filmstrip
        # arrows for order.
        body = self._page()
        self.assertNotIn("memo/drag.js", body)
        for attribute in ("data-drag-handle", "data-drag-kind", "data-drop-kind"):
            self.assertNotIn(attribute, body, f"{attribute} is still on the page")

    def test_a_box_carries_its_own_content_controls(self):
        body = self._page()
        self.assertIn("memo-box-bar", body)
        self.assertIn("setBoxType(", body)
        self.assertIn("openBoxDialog(", body)
        self.assertIn("clearBox(", body)

    def test_the_dialog_brings_its_editors(self):
        # TipTap writes the prose (an ES module resolved through the import
        # map), ACE the code listings. A gap in either is a dialog that cannot
        # type — and Trix must be GONE, or two editors fight over the field.
        body = self._page()
        self.assertIn("memo/tiptap_setup.js", body)
        self.assertIn('type="importmap"', body)
        self.assertIn("@tiptap/core", body)
        self.assertIn("vendor/ace/ace.js", body)
        self.assertNotIn("trix", body.lower())

    def test_layout_names_are_geometry_not_content(self):
        # "Picture, then text" decides for you what goes where. A box takes any
        # kind of content, so the layout may only describe the shape.
        model = (pathlib.Path(pf.__file__).parent
                 / "static" / "memo" / "model.js").read_text()
        # The LAYOUTS array only — BLOCK_TYPES is content and says so.
        block = model.split("var LAYOUTS = [", 1)[1].split("];", 1)[0]
        labels = re.findall(r'label: "([^"]+)"', block)
        self.assertTrue(labels, "no layout labels found")
        for word in ("Picture", "Text", "Image", "Quote", "Statement"):
            for label in labels:
                self.assertNotIn(word, label,
                                 f"layout label {label!r} names content")

    def test_the_toolbar_offers_no_svg_button(self):
        # An SVG is a picture. You insert it with Image — from the vault or an
        # upload — and the block becomes an `svg` when the payload turns out to
        # be markup. Asking the writer which kind of picture file they were
        # about to choose is a question with no useful answer, so the toolbar
        # does not ask it.
        model = (pathlib.Path(pf.__file__).parent
                 / "static" / "memo" / "model.js").read_text()
        types = model.split("var BLOCK_TYPES = [", 1)[1].split("];", 1)[0]
        offered = re.findall(r'id: "([^"]+)"', types)
        self.assertIn("image", offered)
        self.assertNotIn("svg", offered)

    def test_the_svg_block_type_still_exists_on_the_server(self):
        # Dropping the BUTTON is not dropping the type: decks in the wild hold
        # svg blocks, they still render, and the image dialog still creates them.
        self.assertIn("svg", pf.BLOCK_TYPES)

    def test_the_floating_widgets_are_suppressed(self):
        self.assertNotIn("render_floating_plugins", self._page())

    def test_a_stranger_cannot_open_the_editor(self):
        self.client.force_login(User.objects.create_user("bob", password="p"))
        self.assertEqual(
            self.client.get(reverse("memo:edit", args=[self.deck.pk])).status_code, 404)


class MediaUploadTests(TestCase):
    """Files dropped onto a slide are embedded by the server, not the browser."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.client.force_login(self.alice)
        self.url = reverse("memo:media_upload")

    def test_an_svg_is_sanitised_on_the_way_in(self):
        evil = b'<svg onload="alert(1)"><script>x()</script><rect/></svg>'
        data = self.client.post(self.url, {
            "file": SimpleUploadedFile("d.svg", evil, content_type="image/svg+xml")
        }).json()
        self.assertEqual(data["kind"], "svg")
        self.assertNotIn("onload", data["payload"])
        self.assertNotIn("script", data["payload"])

    def test_a_png_comes_back_as_a_self_contained_data_uri(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
        data = self.client.post(self.url, {
            "file": SimpleUploadedFile("d.png", png, content_type="image/png")
        }).json()
        self.assertEqual(data["kind"], "image")
        self.assertTrue(data["payload"].startswith("data:image/"))

    def test_a_non_image_is_refused(self):
        res = self.client.post(self.url, {
            "file": SimpleUploadedFile("d.exe", b"MZ", content_type="application/octet-stream")
        })
        self.assertEqual(res.status_code, 400)

    def test_it_needs_a_login(self):
        self.client.logout()
        res = self.client.post(self.url, {
            "file": SimpleUploadedFile("d.png", b"x", content_type="image/png")})
        self.assertIn(res.status_code, (302, 401, 403))

    def test_a_get_is_refused(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)


class BundleTests(TestCase):
    """The ZIP export, and reading it back."""

    def _deck_with_assets(self):
        return pf.Presentation(title="Talk", theme="white", slides=[
            pf.Slide(title="a", layout="two-column", blocks=[
                pf.Block(type="image", slot="left", attrs={"alt": "Diagram"},
                         payload="data:image/png;base64,aGVsbG8="),
                pf.Block(type="svg", slot="right",
                         payload='<svg viewBox="0 0 1 1"><rect/></svg>'),
                pf.Block(type="text", payload="<p>hi</p>"),
            ])])

    def test_the_archive_holds_readable_xml_and_real_files(self):
        # The whole point: a deck is otherwise megabytes of base64 on one line.
        raw = bundle.export_zip(self._deck_with_assets())
        names = zipfile.ZipFile(io.BytesIO(raw)).namelist()
        self.assertIn("presentation.xml", names)
        self.assertTrue(any(n.endswith(".png") for n in names), names)
        self.assertTrue(any(n.endswith(".svg") for n in names), names)

    def test_the_manifest_carries_no_base64(self):
        raw = bundle.export_zip(self._deck_with_assets())
        xml = zipfile.ZipFile(io.BytesIO(raw)).read("presentation.xml").decode()
        self.assertNotIn("base64", xml)
        self.assertIn('src="assets/', xml)

    def test_export_then_import_returns_the_same_deck(self):
        deck = self._deck_with_assets()
        back = bundle.import_zip(bundle.export_zip(deck))
        self.assertEqual(pf.dumps(back), pf.dumps(deck))

    def test_exporting_does_not_damage_the_deck_it_was_given(self):
        # It is usually the one being rendered; rewriting its payloads to file
        # paths would blank every image on screen.
        deck = self._deck_with_assets()
        bundle.export_zip(deck)
        self.assertTrue(deck.slides[0].blocks[0].payload.startswith("data:"))

    def test_an_image_is_not_re_shrunk_on_every_round_trip(self):
        # Re-running the 640px thumbnailer on import is a slow way to lose a
        # deck: each cycle would make every picture a little smaller.
        deck = self._deck_with_assets()
        once = bundle.import_zip(bundle.export_zip(deck))
        twice = bundle.import_zip(bundle.export_zip(once))
        self.assertEqual(once.slides[0].blocks[0].payload,
                         twice.slides[0].blocks[0].payload)

    def test_a_non_zip_is_refused_with_a_sentence(self):
        with self.assertRaises(bundle.BundleError):
            bundle.import_zip(b"not a zip at all")

    def test_an_archive_without_a_manifest_is_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hello")
        with self.assertRaises(bundle.BundleError) as ctx:
            bundle.import_zip(buf.getvalue())
        self.assertIn("presentation.xml", str(ctx.exception))

    def test_a_traversal_path_cannot_reach_outside_the_archive(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("presentation.xml",
                       '<presentation version="2"><slide><title>t</title>'
                       '<block type="image" src="../../etc/passwd"/></slide>'
                       "</presentation>")
            z.writestr("../../etc/passwd", b"root:x:0:0")
        back = bundle.import_zip(buf.getvalue())
        self.assertEqual(back.slides[0].blocks[0].payload, "")

    def test_a_missing_asset_leaves_an_empty_block_not_a_crash(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("presentation.xml",
                       '<presentation version="2"><slide><title>t</title>'
                       '<block type="image" src="assets/gone.png"/></slide>'
                       "</presentation>")
        back = bundle.import_zip(buf.getvalue())
        self.assertEqual(back.slides[0].blocks[0].payload, "")

    def test_an_imported_archive_is_sanitised(self):
        # An archive is a file somebody handed us, not something we wrote.
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("presentation.xml",
                       '<presentation version="2"><slide><title>t</title>'
                       '<block type="svg" src="assets/x.svg"/></slide></presentation>')
            z.writestr("assets/x.svg", b'<svg onload="alert(1)"><rect/></svg>')
        back = bundle.import_zip(buf.getvalue())
        self.assertNotIn("onload", back.slides[0].blocks[0].payload)


class ExportEndpointTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.deck = VaultFile.objects.create(
            owner=self.alice, title="talk.xml", file_type="presentation",
            bucket=self.bucket,
            file=SimpleUploadedFile("talk.xml", pf.dumps(_deck()).encode()))
        self.client.force_login(self.alice)

    def test_the_zip_downloads_as_an_attachment(self):
        res = self.client.get(reverse("memo:export_zip", args=[self.deck.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/zip")
        self.assertIn("attachment", res["Content-Disposition"])
        self.assertIn("presentation.xml",
                      zipfile.ZipFile(io.BytesIO(res.content)).namelist())

    def test_a_stranger_cannot_export_your_deck(self):
        self.client.force_login(User.objects.create_user("bob", password="p"))
        for name in ("memo:export_zip", "memo:export_pdf"):
            self.assertEqual(
                self.client.get(reverse(name, args=[self.deck.pk])).status_code, 404)

    def test_pdf_without_weasyprint_says_what_to_do(self):
        with mock.patch.object(render_pdf, "render",
                               side_effect=render_pdf.PdfUnavailable(
                                   "needs WeasyPrint … BUILD_WEASYPRINT=1")):
            res = self.client.get(reverse("memo:export_pdf", args=[self.deck.pk]))
        self.assertEqual(res.status_code, 503)
        self.assertIn("BUILD_WEASYPRINT", res.content.decode())

    @skipUnless(render_pdf.is_available(), "WeasyPrint is not installed here")
    def test_the_pdf_renders_one_page_per_slide(self):
        res = self.client.get(reverse("memo:export_pdf", args=[self.deck.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertTrue(res.content.startswith(b"%PDF"))

    def test_the_print_document_uses_the_shared_stylesheet(self):
        from django.template.loader import render_to_string

        html = render_to_string("memo/print.html", {
            "presentation": _deck(), "theme": "white", "slide_css": "/*CSS*/"})
        self.assertIn("/*CSS*/", html)
        self.assertIn("memo-slide", html)
        self.assertIn("Where we landed", html)


class ImportEndpointTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.client.force_login(self.alice)
        self.url = reverse("memo:import")

    def _archive(self):
        return bundle.export_zip(pf.Presentation(title="Restored", slides=[
            pf.Slide(title="a", blocks=[pf.Block(type="text", payload="<p>hi</p>")])]))

    def test_importing_creates_a_new_deck_and_opens_it(self):
        res = self.client.post(self.url, {
            "archive": SimpleUploadedFile("deck.zip", self._archive(),
                                          content_type="application/zip")})
        self.assertEqual(res.status_code, 302)
        deck = VaultFile.objects.get(owner=self.alice, file_type="presentation")
        self.assertIn(str(deck.pk), res["Location"])
        self.assertEqual(pf.loads(deck.file.read().decode()).title, "Restored")

    def test_importing_never_overwrites_an_existing_deck(self):
        # A restore that can destroy the deck you were protecting is the wrong
        # shape for a backup.
        for _ in range(2):
            self.client.post(self.url, {
                "archive": SimpleUploadedFile("deck.zip", self._archive(),
                                              content_type="application/zip")})
        self.assertEqual(
            VaultFile.objects.filter(owner=self.alice, file_type="presentation").count(), 2)

    def test_rubbish_is_reported_not_raised(self):
        res = self.client.post(self.url, {
            "archive": SimpleUploadedFile("x.zip", b"nope", content_type="application/zip")})
        self.assertEqual(res.status_code, 302)
        self.assertFalse(VaultFile.objects.filter(owner=self.alice).exists())

    def test_it_needs_a_login(self):
        self.client.logout()
        res = self.client.post(self.url, {
            "archive": SimpleUploadedFile("d.zip", self._archive())})
        self.assertEqual(res.status_code, 302)
        self.assertIn("login", res["Location"])


class PlayerTests(TestCase):
    """The player is a standalone document with no site chrome."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)

    def _deck(self, xml: str, *, is_public=True) -> VaultFile:
        return VaultFile.objects.create(
            owner=self.alice, title="talk.xml", file_type="xml",
            is_public=is_public, bucket=self.bucket,
            file=SimpleUploadedFile("talk.xml", xml.encode("utf-8")))

    def _present(self, deck):
        return self.client.get(reverse("memo:present", args=[deck.pk]))

    def test_the_page_is_a_single_document(self):
        # The old template emitted a second <body> inside the page body, which
        # the browser drops — taking reveal's viewport sizing with it.
        body = self._present(self._deck(pf.dumps(pf.new_presentation("T")))).content.decode()
        self.assertEqual(body.count("<body"), 1, "nested <body> is back")
        self.assertEqual(body.count("<html"), 1)

    def test_the_site_chrome_does_not_cover_the_deck(self):
        body = self._present(self._deck(pf.dumps(pf.new_presentation("T")))).content.decode()
        for chrome in ("</header>", "</footer>", "Dashboard", "My Profile"):
            self.assertNotIn(chrome, body, f"{chrome} renders over the slideshow")

    def test_the_deck_carries_its_own_theme(self):
        # Not the viewer's dark-mode toggle, and no setInterval polling
        # localStorage for it: a deck looks the same wherever it is presented.
        deck = self._deck(pf.dumps(pf.Presentation(title="T", theme="white",
                                                   slides=[pf.Slide(title="a")])))
        body = self._present(deck).content.decode()
        self.assertIn("memo-theme-white", body)
        self.assertNotIn("theme-black.css", body)
        self.assertNotIn("localStorage", body)

    def test_the_shared_stylesheet_is_what_styles_a_slide(self):
        body = self._present(self._deck(pf.dumps(pf.new_presentation("T")))).content.decode()
        self.assertIn("memo/slide.css", body)
        self.assertIn("vendor/reveal/reveal.css", body, "reveal machinery still needed")

    def test_the_stage_matches_the_stylesheet(self):
        # 1280x720 in both places, or the editor and the player scale
        # differently and the whole isomorphism is a lie.
        body = self._present(self._deck(pf.dumps(pf.new_presentation("T")))).content.decode()
        self.assertIn("width: 1280", body)
        self.assertIn("height: 720", body)

    def test_every_block_type_renders(self):
        deck = self._deck(pf.dumps(_deck()))
        body = self._present(deck).content.decode()
        self.assertIn("Where we landed", body)
        self.assertIn("<li>one</li>", body)
        self.assertIn("data:image/png;base64,", body)
        self.assertIn("<svg", body)
        self.assertIn("Ada", body)
        self.assertIn('data-layout="two-column"', body)

    def test_a_v1_deck_still_presents_its_html(self):
        body = self._present(self._deck(V1_DOC)).content.decode()
        self.assertIn("hand written", body)
        self.assertIn('style="display:flex"', body, "v1 body was rewritten")

    def test_code_is_escaped_not_executed(self):
        deck = self._deck(pf.dumps(pf.Presentation(title="T", slides=[
            pf.Slide(title="a", blocks=[
                pf.Block(type="code", payload="<script>alert(1)</script>")])])))
        body = self._present(deck).content.decode()
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)


class SlideGeometryTests(TestCase):
    """The bugs that made presenting not work, pinned so they cannot come back."""

    def _asset(self, name):
        from django.contrib.staticfiles import finders
        from pathlib import Path

        found = finders.find(name)
        return Path(found).read_text() if found else ""

    def test_the_player_turns_reveal_centring_off(self):
        """Reveal's default `center: true` is what broke the player.

        It positions each <section> from its MEASURED height, and reveal's
        sections are absolutely positioned with height:auto — so a slide sized
        as a percentage of that collapses to nothing, taking every layout that
        depends on the 720px box with it.
        """
        from django.template.loader import get_template

        source = get_template("memo/present.html").template.source
        self.assertIn("center: false", source)
        self.assertIn("width: 1280", source)
        self.assertIn("height: 720", source)

    def test_the_slide_keeps_its_real_size_in_the_player(self):
        css = self._asset("memo/slide.css")
        self.assertTrue(css, "slide.css not found by the staticfiles finders")
        self.assertNotIn(".reveal .slides section .memo-slide {\n  width: 100%;", css,
                         "the percentage override is back — the box will collapse")
        self.assertIn("width: 1280px", css)
        self.assertIn("height: 720px", css)

    def test_an_offscreen_slide_cannot_take_a_click(self):
        # Reveal keeps past/future slides in the DOM for the transition.
        self.assertIn(".reveal .slides section:not(.present) { pointer-events: none; }",
                      self._asset("memo/slide.css"))

    def test_a_press_on_a_control_is_never_a_drag(self):
        """The filmstrip's Delete/Duplicate/arrows were dead because of this.

        The whole row is a drag handle, so `setPointerCapture` fired for a press
        that landed on a button, the pointerup was retargeted to the row, and the
        browser never synthesised a click on the button.
        """
        js = self._asset("memo/drag.js")
        self.assertTrue(js, "drag.js not found")
        self.assertIn("button, a, input, select, textarea, label,"
                      " [contenteditable], [data-no-drag]", js)


class EditorCostTests(TestCase):
    """The editor must not do work proportional to the whole deck per keystroke."""

    def _asset(self, name):
        from django.contrib.staticfiles import finders
        from pathlib import Path

        return Path(finders.find(name)).read_text()

    def test_typing_does_not_snapshot_the_whole_deck_per_keystroke(self):
        """A deck with twenty embedded images is several MB per structuredClone.

        The model still updates synchronously — nothing is at risk — but the
        copy is deferred onto the window that already groups typing into one
        undo step.
        """
        js = self._asset("memo/history.js")
        self.assertIn("flushPending", js)
        self.assertIn("this._pending = state", js)
        # undo/redo must settle the deferred snapshot before reading history
        self.assertIn("this.flushPending();", js)

    def test_offscreen_thumbnails_build_nothing(self):
        from django.template.loader import get_template

        source = get_template("memo/edit.html").template.source
        self.assertIn('x-if="ui.live[slide.id]"', source)
        self.assertIn('x-ref="filmstrip"', source)

    def test_the_live_set_is_component_state_not_a_dom_attribute(self):
        # Alpine tracks its own reactive data; it would never re-evaluate an
        # x-if that read an attribute an IntersectionObserver had changed.
        js = self._asset("memo/canvas.js")
        self.assertIn("onVisible", js)
        self.assertNotIn('setAttribute("data-live"', js)

    def test_thumbnail_text_is_memoised(self):
        js = self._asset("memo/editor.js")
        self.assertIn("_thumbFor", js)

    def test_a_thumbnail_is_inert(self):
        from django.template.loader import get_template

        source = get_template("memo/edit.html").template.source
        thumb = source.split('class="memo-thumb"')[1].split("</template>")[0]
        for live in ("contenteditable", "@input", "x-model"):
            self.assertNotIn(live, thumb, "a thumbnail became editable")


class FormulaTests(TestCase):
    """You write LaTeX; the render is cached so the PDF can show it too."""

    def _block(self, source, render=""):
        deck = pf.Presentation.from_dict({"slides": [{"title": "t", "blocks": [
            {"type": "formula", "payload": source, "render": render}]}]})
        return deck.slides[0].blocks[0]

    def test_a_formula_round_trips_source_and_render(self):
        deck = pf.Presentation(title="T", slides=[pf.Slide(title="a", blocks=[
            pf.Block(type="formula", payload="E = mc^2",
                     render='<span class="katex"><span class="mord">E</span></span>')])])
        back = pf.loads(pf.dumps(deck))
        block = back.slides[0].blocks[0]
        self.assertEqual(block.payload, "E = mc^2")
        self.assertIn("katex", block.render)

    def test_the_source_is_kept_exactly(self):
        # Backslashes and braces are the whole language; escaping them would be
        # the same mistake as sanitising a code block.
        source = r"\frac{-b \pm \sqrt{b^{2}-4ac}}{2a}"
        self.assertEqual(self._block(source).payload, source)

    def test_a_formula_with_no_render_still_round_trips(self):
        deck = pf.loads(pf.dumps(pf.Presentation(slides=[pf.Slide(blocks=[
            pf.Block(type="formula", payload="x^2")])])))
        self.assertEqual(deck.slides[0].blocks[0].payload, "x^2")
        self.assertEqual(deck.slides[0].blocks[0].render, "")

    # -- the cached render is untrusted markup from a browser ---------------
    def test_katex_output_survives_sanitising(self):
        render = ('<span class="katex"><span class="katex-html">'
                  '<span class="mord" style="height:0.8em">E</span></span></span>')
        cleaned = self._block("E", render).render
        self.assertIn("katex-html", cleaned)
        self.assertIn("height:0.8em", cleaned)

    def test_event_handlers_and_foreign_classes_are_stripped(self):
        cleaned = self._block("E", '<span class="katex evil" onclick="x()">E</span>').render
        self.assertNotIn("onclick", cleaned)
        self.assertNotIn("evil", cleaned)
        self.assertIn("katex", cleaned)

    def test_a_script_in_the_render_is_dropped_with_its_contents(self):
        cleaned = self._block("E", '<span class="katex"><script>alert(1)</script>E</span>').render
        self.assertNotIn("script", cleaned)
        self.assertNotIn("alert", cleaned)

    def test_positioning_that_could_escape_the_slide_is_dropped(self):
        cleaned = self._block("E", '<span style="position:fixed;top:0;height:1em">E</span>').render
        self.assertNotIn("fixed", cleaned)
        self.assertIn("height:1em", cleaned)

    def test_a_url_in_a_style_is_dropped(self):
        cleaned = self._block("E", '<span style="background:url(javascript:1)">E</span>').render
        self.assertNotIn("javascript", cleaned)

    def test_mathml_survives(self):
        # KaTeX emits it alongside the visual output, for screen readers.
        cleaned = self._block("E", "<math><mrow><mi>E</mi></mrow></math>").render
        self.assertIn("<mi>E</mi>", cleaned)

    # -- what the surfaces show ---------------------------------------------
    def test_the_player_shows_the_cached_render(self):
        from django.template.loader import render_to_string

        deck = pf.Presentation(title="T", slides=[pf.Slide(title="a", blocks=[
            pf.Block(type="formula", payload="E = mc^2",
                     render='<span class="katex">RENDERED</span>')])])
        html = render_to_string("memo/print.html", {
            "presentation": deck, "theme": "black", "font": "sans",
            "slide_css": "", "katex_css": ""})
        self.assertIn("RENDERED", html)

    def test_without_a_render_the_source_is_shown_not_a_blank(self):
        """A formula you can read as source beats an empty space.

        This is the path taken when the render failed sanitising, or when KaTeX
        was never vendored on this host.
        """
        from django.template.loader import render_to_string

        deck = pf.Presentation(title="T", slides=[pf.Slide(title="a", blocks=[
            pf.Block(type="formula", payload="E = mc^2")])])
        html = render_to_string("memo/print.html", {
            "presentation": deck, "theme": "black", "font": "sans",
            "slide_css": "", "katex_css": ""})
        self.assertIn("memo-formula-source", html)
        self.assertIn("E = mc^2", html)

    def test_a_render_that_is_entirely_rejected_falls_back_to_source(self):
        block = self._block("E = mc^2", '<iframe src="//evil"></iframe>')
        self.assertEqual(block.render, "")
        self.assertEqual(block.payload, "E = mc^2")

    def test_the_pdf_degrades_when_katex_was_never_vendored(self):
        """The vendor directory is gitignored and fetched at image build.

        Its absence is a normal state, not an error — but a stylesheet without
        its fonts would render every glyph as a box, which is worse than the
        source, so both have to be present or neither is used.
        """
        from toto.memo import render_pdf

        with mock.patch("django.contrib.staticfiles.finders.find", return_value=None):
            css, base = render_pdf._katex_assets()
        self.assertEqual(css, "")
        self.assertIsNone(base)


class ScaleTests(TestCase):
    """Auto-fit, the manual override, and how they reach the other surfaces."""

    def test_the_three_shapes_round_trip(self):
        for raw, expect in [("auto", "auto"), ("auto:0.80", "auto:0.80"),
                            ("0.75", "0.75")]:
            deck = pf.Presentation(slides=[pf.Slide(title="a", blocks=[
                pf.Block(type="text", payload="<p>x</p>", attrs={"scale": raw})])])
            back = pf.loads(pf.dumps(deck))
            self.assertEqual(back.slides[0].blocks[0].attrs["scale"], expect, raw)

    def test_a_measurement_never_silently_pins_a_block(self):
        """`auto:0.80` stays auto, so it keeps re-fitting when content changes.

        Storing the resolved number *instead of* the intent would freeze a block
        at whatever size it happened to need once.
        """
        block = pf.Block(type="text", attrs={"scale": "auto:0.80"})
        self.assertTrue(block.attrs["scale"].startswith("auto"))
        self.assertEqual(block.scale_css, "0.80")

    def test_an_out_of_range_scale_is_clamped_not_rejected(self):
        deck = pf.Presentation.from_dict({"slides": [{"title": "a", "blocks": [
            {"type": "text", "attrs": {"scale": "0.05"}},
            {"type": "text", "attrs": {"scale": "9"}},
            {"type": "text", "attrs": {"scale": "nonsense"}}]}]})
        scales = [b.attrs["scale"] for b in deck.slides[0].blocks]
        self.assertEqual(scales, [f"{pf.MIN_SCALE:.2f}", f"{pf.MAX_SCALE:.2f}", "auto"])

    def test_a_full_size_block_emits_no_style(self):
        # Otherwise every block on every slide carries a redundant inline style.
        for raw in ("auto", "1.00", ""):
            self.assertEqual(pf.Block(type="text", attrs={"scale": raw}).scale_css, "")

    def test_an_enlarged_block_keeps_its_size(self):
        # Auto-fit only ever shrinks; the manual override goes both ways.
        self.assertEqual(pf.Block(type="text", attrs={"scale": "1.60"}).scale_css, "1.60")

    def test_the_resolved_scale_reaches_the_player(self):
        from django.template.loader import render_to_string

        deck = pf.Presentation(title="T", slides=[pf.Slide(title="a", blocks=[
            pf.Block(type="text", payload="<p>small</p>",
                     attrs={"scale": "auto:0.65"})])])
        html = render_to_string("memo/print.html", {
            "presentation": deck, "theme": "black", "font": "sans", "slide_css": ""})
        self.assertIn("--memo-block-scale: 0.65", html)

    def test_the_block_scale_does_not_share_the_stage_zoom_property(self):
        """Custom properties inherit.

        The stage sets `--memo-scale` on an ancestor to zoom the whole slide. If
        the per-block font multiplier used the same name, every block on a
        scaled-down canvas would shrink its text by the zoom factor too.
        """
        from django.contrib.staticfiles import finders
        from pathlib import Path

        css = Path(finders.find("memo/slide.css")).read_text()
        self.assertIn("font-size: calc(1em * var(--memo-block-scale, 1))", css)
        self.assertIn("transform: scale(var(--memo-scale, 1))", css)


class FontTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="Toto", author="T",
                                publication_year=2026, active=True)
        self.alice = User.objects.create_user("alice", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)

    def _deck_file(self, font="serif", public=True):
        deck = _deck()
        deck.font = font
        return VaultFile.objects.create(
            owner=self.alice, title="talk.xml", file_type="presentation",
            is_public=public, bucket=self.bucket,
            file=SimpleUploadedFile("talk.xml", pf.dumps(deck).encode()))

    def test_the_font_round_trips(self):
        for font in pf.FONTS:
            deck = pf.Presentation(title="T", font=font, slides=[pf.Slide(title="a")])
            self.assertEqual(pf.loads(pf.dumps(deck)).font, font)

    def test_an_unknown_font_falls_back(self):
        self.assertEqual(
            pf.loads('<presentation version="2" font="comic-sans"/>').font, "sans")

    def test_a_deck_with_no_font_is_sans(self):
        self.assertEqual(pf.loads('<presentation version="2"/>').font, "sans")

    def test_the_font_reaches_the_player(self):
        deck = self._deck_file("serif")
        body = self.client.get(
            reverse("memo:present", args=[deck.pk])).content.decode()
        self.assertIn("memo-font-serif", body)

    def test_the_font_reaches_the_print_document(self):
        from django.template.loader import render_to_string

        deck = _deck()
        deck.font = "mono"
        html = render_to_string("memo/print.html", {
            "presentation": deck, "theme": deck.theme, "font": deck.font,
            "slide_css": ""})
        self.assertIn("memo-font-mono", html)

    def test_the_font_reaches_the_gallery_card(self):
        self._deck_file("condensed")
        self.client.force_login(self.alice)
        body = self.client.get(reverse("memo:index")).content.decode()
        self.assertIn("memo-font-condensed", body)

    def test_the_font_reaches_the_editor(self):
        deck = self._deck_file("rounded", public=False)
        self.client.force_login(self.alice)
        body = self.client.get(reverse("memo:edit", args=[deck.pk])).content.decode()
        chunk = body.split('id="memo-data"')[1].split("</script>")[0]
        self.assertEqual(json.loads(chunk.split(">", 1)[1])["font"], "rounded")

    def test_every_font_has_a_stack_and_ends_in_a_generic(self):
        """A family the container lacks would silently substitute in the PDF.

        Every stack must end in a CSS generic, which fontconfig can always
        resolve inside the image — otherwise the exported deck stops matching
        what the author saw, which is the one thing slide.css exists to prevent.
        """
        from django.contrib.staticfiles import finders
        from pathlib import Path

        css = Path(finders.find("memo/slide.css")).read_text()
        for font in pf.FONTS:
            marker = f".memo-slide.memo-font-{font} {{"
            self.assertIn(marker, css, font)
            stack = css.split(marker)[1].split("}")[0]
            self.assertTrue(
                any(g in stack for g in ("sans-serif;", "serif;", "monospace;")),
                f"{font} stack does not end in a generic: {stack.strip()}")


class VaultDetectionTests(TestCase):
    def test_pml_extension_retired(self):
        # The dedicated `.pml`/presentation vault type is retired — presentations
        # are ordinary .xml now, so `.pml` no longer maps to a special type.
        self.assertNotEqual(VaultFile.detect_type("", "talk.pml"), "presentation")
        self.assertEqual(VaultFile.detect_type("application/xml", "deck.xml"), "xml")

    def test_generic_xml_stays_xml(self):
        self.assertEqual(VaultFile.detect_type("application/xml", "data.xml"), "xml")
        self.assertEqual(VaultFile.detect_type("text/xml", "feed.xml"), "xml")


class PresentationVaultIntegrationTests(TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        Platform.objects.create(
            site_name="Toto", author="Test", publication_year=2026, active=True
        )

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(
            name="Talks", bucket=self.bucket, owner=self.alice
        )

    def _make_presentation(self, p=None, owner=None, is_public=False) -> VaultFile:
        owner = owner or self.alice
        p = p or pf.new_presentation("Analysis")
        return VaultFile.objects.create(
            owner=owner,
            title="talk.pml",
            file_type="presentation",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile("talk.pml", pf.dumps(p).encode("utf-8")),
        )

    def test_plugins_registered_for_presentation(self):
        play = VaultPlayPlugin.for_file_type("presentation")
        editor = VaultEditorPlugin.for_file_type("presentation")
        self.assertIsNotNone(play)
        self.assertIsNotNone(editor)
        vf = self._make_presentation()
        self.assertEqual(play.get_play_url(vf), reverse("memo:present", args=[vf.pk]))
        # Edit opens the slide editor. It used to open the raw XML, which made
        # the vault's most obvious entry point the least useful one.
        self.assertEqual(editor.get_editor_url(vf), reverse("memo:edit", args=[vf.pk]))

    def test_a_legacy_xml_deck_is_adopted_on_open(self):
        """A deck filed as generic XML gets its type repaired when memo sees it.

        While decks were typed `xml` they had no Play button at all and Edit
        opened the generic XML editor, because a plugin only fires when its
        `key` equals a file_type and `xml` belongs to toto.editor. Repairing the
        row on first touch beats migrating every host's vault.
        """
        vf = self._make_presentation()
        VaultFile.objects.filter(pk=vf.pk).update(file_type="xml")

        self.client.force_login(self.alice)
        self.client.get(reverse("memo:present", args=[vf.pk]))

        vf.refresh_from_db()
        self.assertEqual(vf.file_type, "presentation")
        self.assertIsNotNone(VaultPlayPlugin.for_file_type(vf.file_type))

    def test_the_raw_xml_editor_is_gone(self):
        """Edit opens the block editor; there is no angle-bracket surface left.

        It carried the gitvault toolbar, which moved onto the editor rather than
        being lost with it.
        """
        from django.urls import NoReverseMatch

        for name in ("memo:source", "memo:source_save"):
            with self.assertRaises(NoReverseMatch):
                reverse(name, args=[1])

    def test_the_editor_carries_the_git_toolbar(self):
        """The history buttons moved here rather than dying with the XML page.

        gitvault is a host app and is not installed under memo's own settings,
        so `gitvault_context` correctly returns nothing here — patching it is
        what proves the editor asks for it at all.
        """
        from toto.editor.views import BaseFileDisplayView

        vf = self._make_presentation()
        self.client.force_login(self.alice)
        # A marker key rather than `gitvault_ctx` itself: the template's include
        # of gitvault's partial is guarded on that name, and forcing it truthy
        # on a host where gitvault is not installed would fail on a missing
        # template — which is the guard doing its job, not a bug.
        with mock.patch.object(BaseFileDisplayView, "gitvault_context",
                               return_value={"git_probe": "consulted"}):
            res = self.client.get(reverse("memo:edit", args=[vf.pk]))
        self.assertEqual(res.context["git_probe"], "consulted")

    def test_the_editor_guards_the_git_include(self):
        # A host with memo but without gitvault must still render the editor.
        from django.template.loader import get_template

        source = get_template("memo/edit.html").template.source
        self.assertIn("{% if gitvault_ctx %}", source)

    def test_a_plain_xml_file_still_belongs_to_the_generic_editor(self):
        """The assertion that stops memo hijacking every XML file in the vault."""
        vf = VaultFile.objects.create(
            owner=self.alice, title="data.xml", file_type="xml",
            bucket=self.bucket, directory=self.directory,
            file=SimpleUploadedFile("data.xml", b"<rows><row/></rows>"))
        self.assertIsNone(VaultPlayPlugin.for_file_type(vf.file_type))
        self.assertEqual(VaultEditorPlugin.for_file_type(vf.file_type).get_key(), "xml")
        # And it cannot be opened as a deck.
        self.client.force_login(self.alice)
        self.assertEqual(
            self.client.get(reverse("memo:present", args=[vf.pk])).status_code, 404)

    def test_view_public_ok_for_anon(self):
        vf = self._make_presentation(is_public=True)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)

    def test_view_private_denied_for_anon(self):
        vf = self._make_presentation(is_public=False)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        # redirect_to_login → 302
        self.assertEqual(res.status_code, 302)

    def test_view_private_ok_for_owner(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", blocks=[pf.Block(type="html", payload="<p>body one</p>")])])
        vf = self._make_presentation(p=p)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:present", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "body one")

    def test_edit_hydration_and_owner_only(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", blocks=[pf.Block(type="html", payload="<p>x</p>")])])
        vf = self._make_presentation(p=p)

        # non-owner → 404
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(reverse("memo:edit", args=[vf.pk])).status_code, 404
        )

        # owner → 200, hydration payload present
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:edit", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        self.assertIn('id="memo-data"', body)
        start = body.index('id="memo-data"')
        snippet = body[start:body.index("</script>", start)]
        payload = json.loads(snippet[snippet.index(">") + 1:])
        self.assertEqual(payload["slides"][0]["title"], "S1")
        self.assertIn(reverse("memo:save", args=[vf.pk]), body)

    def test_save_serialises_to_file(self):
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        payload = {
            "title": "Updated",
            "slides": [
                {"title": "One", "body": '<img src="data:image/png;base64,AAAA" alt="a">'},
                {"title": "Two", "body": "<p>second</p>"},
            ],
        }
        res = self.client.post(
            reverse("memo:save", args=[vf.pk]),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        back = pf.loads(saved)
        self.assertEqual(back.title, "Updated")
        self.assertEqual(len(back.slides), 2)
        self.assertIn("data:image/png;base64,AAAA", back.slides[0].body)

    def test_save_denied_for_non_owner(self):
        vf = self._make_presentation()
        self.client.force_login(self.bob)
        res = self.client.post(
            reverse("memo:save", args=[vf.pk]),
            data=json.dumps({"slides": []}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)

    def test_presentation_type_retired_from_vault_new_file(self):
        from toto.vault.views import CREATABLE_TYPES, CreateEmptyFileView
        self.assertNotIn("presentation", {t for t, _ in CREATABLE_TYPES})
        self.assertNotIn("presentation", CreateEmptyFileView._ALLOWED)
        self.assertNotIn("presentation", CreateEmptyFileView._INITIAL)

    def test_present_404_on_non_presentation_xml(self):
        vf = VaultFile.objects.create(
            owner=self.alice, title="notes.xml", file_type="xml",
            is_public=True, bucket=self.bucket,
            file=SimpleUploadedFile("notes.xml", b"<notes><a/></notes>"),
        )
        self.assertEqual(self.client.get(reverse("memo:present", args=[vf.pk])).status_code, 404)

    def test_index_lists_presentations(self):
        self._make_presentation(is_public=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:index"))
        self.assertEqual(res.status_code, 200)

    def test_create_new_presentation_redirects_to_editor(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice,
                                      file_type="presentation").latest("pk")
        self.assertEqual(res.url, reverse("memo:edit", args=[vf.pk]))
        # Created in the user's personal bucket with a valid blank deck.
        self.assertEqual(vf.bucket.slug, f"personal-{self.alice.username}")
        self.assertTrue(vf.title.endswith(".xml"))
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(len(pf.loads(content).slides), 1)

    def test_create_requires_login(self):
        res = self.client.post(reverse("memo:create"))
        self.assertEqual(res.status_code, 302)  # redirect to login
        self.assertIn("/login", res.url)

    def test_create_into_chosen_bucket_and_directory(self):
        self.client.force_login(self.alice)
        res = self.client.post(reverse("memo:create"), data={
            "filename": "My Talk",
            "bucket_id": str(self.bucket.pk),
            "directory_id": str(self.directory.pk),
        })
        self.assertEqual(res.status_code, 302)
        vf = VaultFile.objects.filter(owner=self.alice,
                                      file_type="presentation").latest("pk")
        self.assertEqual(res.url, reverse("memo:edit", args=[vf.pk]))
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.directory, self.directory)
        self.assertEqual(vf.title, "My Talk.xml")
        self.assertEqual(vf.key, "my-talk")
        with vf.file.open("r") as f:
            content = f.read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        self.assertEqual(len(pf.loads(content).slides), 1)

    def test_create_rejects_foreign_bucket(self):
        self.client.force_login(self.bob)
        res = self.client.post(reverse("memo:create"), data={
            "filename": "sneaky",
            "bucket_id": str(self.bucket.pk),
        })
        self.assertEqual(res.status_code, 404)
        self.assertFalse(VaultFile.objects.filter(owner=self.bob).exists())

    def test_index_shows_location_path(self):
        self._make_presentation(is_public=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:index"))
        self.assertContains(res, "Lab / Talks")


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class PresentationMediaEmbedTests(TestCase):
    """Insert an image or SVG from a vault bucket into a slide."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)

        Platform.objects.create(
            site_name="Toto", author="Test", publication_year=2026, active=True
        )

        self.alice = User.objects.create_user("alice", password="pass")
        self.bob = User.objects.create_user("bob", password="pass")
        self.bucket = Bucket.objects.create(name="Lab", slug="lab", owner=self.alice)
        self.directory = VaultDirectory.objects.create(
            name="Assets", bucket=self.bucket, owner=self.alice
        )
        self.deck = VaultFile.objects.create(
            owner=self.alice,
            title="talk.pml",
            file_type="presentation",
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile("talk.pml", pf.dumps(pf.new_presentation()).encode()),
        )

    def _make_image(self, owner=None, is_public=False, title="pic.png"):
        owner = owner or self.alice
        return VaultFile.objects.create(
            owner=owner,
            title=title,
            file_type="image",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile(title, _TINY_PNG, content_type="image/png"),
        )

    def _make_svg(self, owner=None, is_public=False, title="logo.svg", markup=None):
        owner = owner or self.alice
        markup = markup or '<svg xmlns="http://www.w3.org/2000/svg"><rect width="4" height="4"/></svg>'
        return VaultFile.objects.create(
            owner=owner,
            title=title,
            file_type="svg",
            is_public=is_public,
            bucket=self.bucket,
            directory=self.directory,
            file=SimpleUploadedFile(title, markup.encode("utf-8"), content_type="image/svg+xml"),
        )

    # ── Picker payload in the editor page ───────────────────────────

    def test_edit_page_lists_media_with_location(self):
        self._make_image(title="pic.png")
        self._make_svg(title="logo.svg")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:edit", args=[self.deck.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        # The vault-media picker payload + its "location" path are hydrated.
        self.assertIn('id="memo-media"', body)
        self.assertIn("pic.png", body)
        self.assertIn("logo.svg", body)
        self.assertIn("Lab / Assets", body)

    # ── Embed endpoint ──────────────────────────────────────────────

    def test_embed_image_returns_data_uri(self):
        img = self._make_image()
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["kind"], "image")
        self.assertTrue(data["data_uri"].startswith("data:image/"))
        self.assertIn(";base64,", data["data_uri"])
        self.assertEqual(data["alt"], "pic")

    def test_embed_svg_inlines_and_strips_scripts(self):
        svg = self._make_svg(
            markup='<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg">'
            '<script>alert(1)</script><rect width="4" height="4"/></svg>'
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": svg.pk})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["kind"], "svg")
        self.assertIn("<svg", data["markup"])
        self.assertIn("<rect", data["markup"])
        self.assertNotIn("<script", data["markup"])
        self.assertNotIn("<?xml", data["markup"])

    def test_embed_allows_public_file_of_other_user(self):
        img = self._make_image(owner=self.bob, is_public=True, title="shared.png")
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["kind"], "image")

    def test_embed_denies_private_file_of_other_user(self):
        # Bob's private image in Bob's OWN bucket — Alice has no access path.
        # (A file in Alice's bucket would be readable by her as bucket owner.)
        bob_bucket = Bucket.objects.create(name="Bob", slug="bob", owner=self.bob)
        img = VaultFile.objects.create(
            owner=self.bob,
            title="secret.png",
            file_type="image",
            is_public=False,
            bucket=bob_bucket,
            file=SimpleUploadedFile("secret.png", _TINY_PNG, content_type="image/png"),
        )
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 404)

    def test_embed_skips_encrypted_file(self):
        img = self._make_image()
        VaultFile.objects.filter(pk=img.pk).update(is_encrypted=True)
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 404)

    def test_embed_requires_login(self):
        img = self._make_image()
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": img.pk})
        self.assertEqual(res.status_code, 302)
        self.assertIn("/login", res.url)

    def test_embed_bad_pk_is_400(self):
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:media_embed"), {"file_pk": "abc"})
        self.assertEqual(res.status_code, 400)


class LayoutBoxTests(TestCase):
    """A layout is a fixed set of boxes, and every box takes every block type."""

    def test_every_layout_names_its_boxes(self):
        for layout in pf.LAYOUTS:
            with self.subTest(layout=layout):
                slots = pf.slots_for(layout)
                self.assertTrue(slots, f"{layout} has no boxes")
                self.assertEqual(len(slots), len(set(slots)), "duplicate box")

    def test_the_stylesheet_places_every_box(self):
        # A box with no grid-area lands wherever the browser feels like, which
        # is the one failure mode nothing else here would catch.
        css = (pathlib.Path(pf.__file__).parent
               / "static" / "memo" / "slide.css").read_text()
        for layout, slots in pf.LAYOUT_SLOTS.items():
            for slot in slots:
                if not slot:
                    continue
                with self.subTest(layout=layout, slot=slot):
                    self.assertIn(f'[data-layout="{layout}"] .memo-flow[data-slot="{slot}"]',
                                  css)

    def test_the_javascript_agrees_about_the_boxes(self):
        # The Python side groups blocks for the player, the thumbnail and the
        # PDF; the JS groups them for the editor. A disagreement shows as a
        # block that moves when you press Present.
        js = (pathlib.Path(pf.__file__).parent
              / "static" / "memo" / "model.js").read_text()
        for layout, slots in pf.LAYOUT_SLOTS.items():
            with self.subTest(layout=layout):
                self.assertIn(f'id: "{layout}"', js)
                for slot in slots:
                    if slot:
                        self.assertIn(f'"{slot}"', js)

    def test_a_block_lands_in_the_box_its_slot_names(self):
        slide = pf.Slide(id="s1", layout="grid", blocks=[
            pf.Block(id="b1", type="text", slot="c"),
            pf.Block(id="b2", type="image", slot="a"),
        ])
        boxes = {slot: [b.id for b in blocks] for slot, blocks in slide.columns}
        self.assertEqual(boxes, {"a": ["b2"], "b": [], "c": ["b1"], "d": []})

    def test_a_block_whose_box_this_layout_lacks_shows_in_the_first_one(self):
        slide = pf.Slide(id="s1", layout="title-content", blocks=[
            pf.Block(id="b1", type="text", slot="right"),
        ])
        self.assertEqual([(s, [b.id for b in bs]) for s, bs in slide.columns],
                         [("", ["b1"])])

    def test_switching_layout_and_back_puts_everything_where_it_was(self):
        # The block keeps its own slot in the file even while it renders
        # elsewhere — losing it would shuffle the deck on every layout try.
        deck = pf.new_presentation("Boxes")
        slide = deck.slides[0]
        slide.layout = "two-column"
        slide.blocks = [pf.Block(id="b1", type="text", slot="right")]
        slide.layout = "quote"
        deck = pf.loads(pf.dumps(deck))
        deck.slides[0].layout = "two-column"
        self.assertEqual(deck.slides[0].columns[1][1][0].id, "b1")

    def test_every_block_type_is_allowed_in_every_box(self):
        # The point of the rework: a box is a place, not a type.
        for block_type in pf.BLOCK_TYPES:
            with self.subTest(block_type=block_type):
                deck = pf.new_presentation("Any")
                deck.slides[0].layout = "grid"
                deck.slides[0].blocks = [
                    pf.Block(id="b1", type=block_type, slot="d", payload="x")]
                back = pf.loads(pf.dumps(deck))
                self.assertEqual(back.slides[0].blocks[0].slot, "d")
                self.assertEqual(back.slides[0].blocks[0].type, block_type)


class DeletionTests(TestCase):
    """Deleting a deck is the VAULT's delete, surfaced — not a second one.

    memo adds only buttons; `vault:delete_file` owns the rules (owner-only,
    bytes and row together). These tests pin the surfacing and the reuse.
    """

    def setUp(self):
        from django.core.files.base import ContentFile
        self._content_file = ContentFile
        # A scratch MEDIA_ROOT, like every file-touching class here: the real
        # one is a root-owned docker bind mount on a deployed checkout.
        self._tmp = tempfile.mkdtemp()
        self._override = override_settings(MEDIA_ROOT=self._tmp)
        self._override.enable()
        self.addCleanup(self._override.disable)
        Platform.objects.create(site_name="T", author="t", publication_year=2026)
        self.owner = User.objects.create_user("deck-owner", password="p")
        self.other = User.objects.create_user("deck-other", password="p")
        self.bucket = Bucket.objects.create(
            slug="del", name="Del", owner=self.owner, storage_backend="local")
        deck = pf.new_presentation(title="Doomed")
        self.vf = VaultFile(owner=self.owner, title="doomed.pml", key="doomed",
                            file_type="presentation", bucket=self.bucket,
                            is_public=True, is_encrypted=False)
        self.vf.file.save("doomed.pml",
                          self._content_file(pf.dumps(deck).encode()),
                          save=True)

    def test_the_gallery_offers_delete_to_the_owner_only(self):
        self.client.force_login(self.owner)
        body = self.client.get(reverse("memo:index")).content.decode()
        self.assertIn("destroy(%d" % self.vf.pk, body)
        self.client.force_login(self.other)
        body = self.client.get(reverse("memo:index")).content.decode()
        self.assertNotIn("destroy(%d" % self.vf.pk, body)

    def test_the_editor_offers_delete_through_the_vault(self):
        self.client.force_login(self.owner)
        body = self.client.get(reverse("memo:edit", args=[self.vf.pk])).content.decode()
        self.assertIn("Delete presentation", body)
        self.assertIn(reverse("vault:delete_file"), body)

    def test_the_vault_endpoint_deletes_a_deck_for_its_owner_only(self):
        self.client.force_login(self.other)
        self.assertEqual(self.client.post(
            reverse("vault:delete_file"), {"file_pk": self.vf.pk}).status_code, 404)
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("vault:delete_file"), {"file_pk": self.vf.pk})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(VaultFile.objects.filter(pk=self.vf.pk).exists())


class TipTapVendorTests(SimpleTestCase):
    """The vendored TipTap closure and the import map that resolves it.

    These files live in memo because both editors in this wheel run on TipTap
    and only one app can own 54 vendored modules; cyprian imports memo, never
    the other way round, so the shared half sits here. cyprian's own setup
    module is checked in `toto.cyprian.tests.test_tiptap`.

    An import map is easy to get wrong in a way nothing else catches: a missing
    entry is a module that 404s in a browser, after a deploy, with the editor
    simply not appearing. These tests are the reason that cannot happen quietly.
    """

    def test_the_manifest_is_not_empty(self):
        self.assertTrue(tiptap.manifest(),
                        "no vendored TipTap — run scripts/fetch_tiptap.py")

    def test_every_manifest_entry_has_a_file(self):
        for spec, name in tiptap.manifest().items():
            with self.subTest(spec=spec):
                self.assertTrue((tiptap.VENDOR_DIR / name).is_file(), name)

    def test_the_specifiers_the_slide_editor_imports_are_all_there(self):
        # If one of these is missing, tiptap_setup.js fails on its first import
        # and the box dialog has no prose field at all.
        setup = (tiptap.VENDOR_DIR.parent.parent / "tiptap_setup.js")
        for spec in tiptap.bare_imports(setup.read_text(encoding="utf-8")):
            with self.subTest(spec=spec):
                self.assertIn(spec, tiptap.manifest())

    def test_the_map_resolves_every_import_in_every_vendored_file(self):
        # The closure test. A gap here is a 404 in production and nothing else
        # in this repo would notice.
        self.assertEqual(tiptap.missing_specifiers(), {})

    def test_no_vendored_file_is_an_mjs(self):
        # Production serves /static/ straight from nginx with stock mime.types,
        # which has no .mjs entry — the file would go out as
        # application/octet-stream and the browser would refuse the module.
        # Whitenoise (dev) knows .mjs, so this would break ONLY in production.
        for name in tiptap.manifest().values():
            with self.subTest(name=name):
                self.assertFalse(name.endswith(".mjs"), name)

    def test_no_vendored_file_carries_a_sourcemap_comment(self):
        # We do not vendor the .map, and the resilient storage would rather not
        # see the reference at all.
        for name in tiptap.manifest().values():
            text = (tiptap.VENDOR_DIR / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"(?m)^//# sourceMappingURL=", text))

    def test_the_pin_is_written_down(self):
        version = (tiptap.VENDOR_DIR / "VERSION.txt").read_text()
        self.assertIn("TipTap", version)
        self.assertIn("import map", version)
        self.assertTrue((tiptap.VENDOR_DIR / "LICENSE").is_file())

    def test_it_is_a_valid_import_map(self):
        parsed = json.loads(tiptap.import_map_json().replace("\\u003c", "<"))
        self.assertIn("imports", parsed)
        self.assertIn("@tiptap/core", parsed["imports"])

    def test_every_url_goes_through_static(self):
        for spec, url in tiptap.import_map()["imports"].items():
            with self.subTest(spec=spec):
                self.assertTrue(url.startswith("/static/"), url)

    def test_a_less_than_sign_cannot_close_the_script_element(self):
        self.assertNotIn("<", tiptap.import_map_json())
