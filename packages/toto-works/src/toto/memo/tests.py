"""Tests for the file-based presentation format + vault-wired viewer/editor."""

from __future__ import annotations

import base64
import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.memo import presentation_format as pf
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
        self.assertEqual(self._block("text", "<div><font>words</font></div>").payload,
                         "words")

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
        # The vault Edit button opens the plain-text XML source editor; the
        # structured slide editor stays reachable from the memo app itself.
        self.assertEqual(editor.get_editor_url(vf), reverse("memo:source", args=[vf.pk]))

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
        self.assertIn('id="presentation-data"', body)
        start = body.index('id="presentation-data"')
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
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
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
        vf = VaultFile.objects.filter(owner=self.alice, file_type="xml").latest("pk")
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

    def test_source_view_owner_only(self):
        p = pf.Presentation(title="T", slides=[pf.Slide(title="S1", blocks=[pf.Block(type="html", payload="<p>x</p>")])])
        vf = self._make_presentation(p=p)

        # non-owner → 404
        self.client.force_login(self.bob)
        self.assertEqual(
            self.client.get(reverse("memo:source", args=[vf.pk])).status_code, 404
        )

        # owner → 200 with the raw XML in the page
        self.client.force_login(self.alice)
        res = self.client.get(reverse("memo:source", args=[vf.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.content.decode()
        # The raw XML is hydrated into Ace via |escapejs, so `<presentation`
        # appears as the escaped literal.
        self.assertIn("\\u003Cpresentation", body)
        self.assertIn(reverse("memo:source_save", args=[vf.pk]), body)
        # Toolbar links back into the memo app.
        self.assertIn(reverse("memo:edit", args=[vf.pk]), body)
        self.assertIn(reverse("memo:present", args=[vf.pk]), body)

    def test_source_save_round_trip(self):
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        xml = pf.dumps(
            pf.Presentation(title="Raw", slides=[pf.Slide(title="One", blocks=[pf.Block(type="html", payload="<p>hi</p>")])])
        )
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": xml}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")
        self.assertTrue(res.json()["valid_presentation"])

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        self.assertEqual(saved, xml)
        self.assertEqual(pf.loads(saved).title, "Raw")

    def test_source_save_accepts_invalid_xml_but_flags_it(self):
        # Plain-text editing must not gate on parse state — the save lands,
        # the response just reports the file no longer parses.
        vf = self._make_presentation()
        self.client.force_login(self.alice)
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": "<broken"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")
        self.assertFalse(res.json()["valid_presentation"])

        vf.refresh_from_db()
        with vf.file.open("r") as f:
            saved = f.read()
        if isinstance(saved, bytes):
            saved = saved.decode("utf-8")
        self.assertEqual(saved, "<broken")

    def test_source_save_denied_for_non_owner(self):
        vf = self._make_presentation()
        self.client.force_login(self.bob)
        res = self.client.post(
            reverse("memo:source_save", args=[vf.pk]), data={"content": "x"}
        )
        self.assertEqual(res.status_code, 404)


# 1×1 transparent PNG — small, real raster that Pillow can decode.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


class PresentationMediaEmbedTests(TestCase):
    """Insert image/SVG from a vault bucket into a slide (self-contained embed)."""

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
        self.assertIn('id="vault-media-data"', body)
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
