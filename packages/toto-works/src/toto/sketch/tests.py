"""The Sketch suite — run against ``toto.sketch.testing.settings``.

The behavioural weight sits server-side: screening (files are refused, never
rewritten), the verbatim save round-trip, ownership, the lock and conflict
rules, and the vault "open" routing.

The screening RULES are not tested here any more — they live in
``toto.antivirus`` and are tested there. What is tested here is that this app
asks, and obeys the answer. The editor itself is browser JavaScript, exercised by the
hosts' page smokes; its authoritative geometry spec lives with the enigma
tests it was ported from.
"""

import tempfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault.models import VaultFile
from toto.vault.plugins import VaultEditorPlugin

from toto.vault.scanning import scan
from .views import EMPTY_SVG, SAVE_MAX_BYTES, _read_raw


def make_svg(owner, title="drawing", is_public=False, text=EMPTY_SVG):
    """An ``svg`` vault file seeded the way SketchCreateView seeds one."""
    vf = VaultFile(
        owner=owner,
        title=f"{title}.svg",
        key=title,
        file_type="svg",
        is_public=is_public,
    )
    vf.file.save(f"{title}.svg", ContentFile(text.encode("utf-8")), save=False)
    vf.content_hash = vf.create_hash()
    vf.save()
    return vf


SKETCH_AUTHORED = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" '
    'viewBox="0 0 1920 1080" data-en-bg="#ffffff">\n'
    '  <path d="M10 10 Q 15 15 20 20" data-en-pts="10 10 20 20" fill="none" '
    'stroke="#111827" stroke-width="4" opacity="1" stroke-linecap="round"/>\n'
    '  <rect x="5" y="5" width="10" height="10" fill="#ef4444" '
    'stroke="#111827" stroke-width="2" opacity="0.75"/>\n'
    '  <text x="30" y="30" font-size="24" fill="#111827">Hello</text>\n'
    '  <line x1="0" y1="0" x2="50" y2="50" stroke="#111827" stroke-width="4" '
    'opacity="1" stroke-linecap="round"/>\n'
    '  <polygon points="0,0 100,0 50,80" fill="#ef4444" stroke="#111827" '
    'stroke-width="2" opacity="1" stroke-linejoin="round"/>\n'
    '  <path d="M0 0A72.5 72.5 0 0 0 100 0" data-en-arc="0 0 100 0 20" '
    'fill="none" stroke="#111827" stroke-width="4" opacity="1" '
    'stroke-linecap="round"/>\n'
    '</svg>'
)

UPLOADED_PLAIN = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
    '<g transform="translate(3,4)"><circle cx="10" cy="10" r="5" fill="red"/>'
    '<polyline points="1,2 3,4 5,6" stroke="black" fill="none"/></g>'
    '<a href="#part"><rect x="1" y="1" width="4" height="4"/></a>'
    '</svg>'
)


class ScreeningTests(TestCase):
    """That sketch asks the scanner, and obeys it.

    Sketch used to own these rules in a private ``svg_guard`` module. It does
    not any more — ``toto.antivirus`` does, and the reason names below are its
    vocabulary, not this app's. The exhaustive rule table lives in that app's
    tests; what belongs here is the handful of cases this editor's own
    behaviour depends on.
    """

    def _refusal(self, text):
        verdict = scan(text, file_type="svg")
        self.assertFalse(verdict.ok, "expected a refusal")
        self.assertTrue(verdict.scanned, "the app must be installed for this suite")
        return verdict

    def test_accepts_the_editor_outputs(self):
        """The round trip that must never break: what we write, we can reopen."""
        self.assertTrue(scan(EMPTY_SVG, file_type="svg").ok)
        self.assertTrue(scan(SKETCH_AUTHORED, file_type="svg").ok)

    def test_accepts_a_plain_uploaded_svg(self):
        self.assertTrue(scan(UPLOADED_PLAIN, file_type="svg").ok)

    def test_accepts_inline_raster_images(self):
        ok = ('<svg xmlns="http://www.w3.org/2000/svg">'
              '<image href="data:image/png;base64,AAAA"/></svg>')
        self.assertTrue(scan(ok, file_type="svg").ok)

    def test_refuses_active_content(self):
        for tag in ("script", "foreignObject", "animate", "set",
                    "iframe", "object", "embed", "sCrIpT"):
            svg = f'<svg xmlns="http://www.w3.org/2000/svg"><{tag}/></svg>'
            with self.subTest(tag=tag):
                self.assertEqual(self._refusal(svg).reason, "active-content")

    def test_a_sprite_reference_is_allowed_and_an_outward_one_is_not(self):
        """The rule that changed when the guard moved, and why.

        The old private guard refused every ``<use>``. Every icon sprite in the
        world is ``<use href="#icon">``, so that refused ordinary drawings —
        which is how people learn to ignore a scanner. What is actually
        dangerous is a ``<use>`` pointing OUTSIDE the document, and that is
        still refused, by the reference rule rather than the tag rule.
        """
        inward = '<svg xmlns="http://www.w3.org/2000/svg"><use href="#shape"/></svg>'
        self.assertTrue(scan(inward, file_type="svg").ok)

        outward = '<svg xmlns="http://www.w3.org/2000/svg"><use href="//evil/x#y"/></svg>'
        self.assertEqual(self._refusal(outward).reason, "external-reference")

    def test_refuses_event_handlers(self):
        self.assertEqual(
            self._refusal('<svg xmlns="http://www.w3.org/2000/svg">'
                          '<rect onload="alert(1)" width="4" height="4"/></svg>').reason,
            "active-content")

    def test_refuses_external_references(self):
        cases = (
            '<image href="https://evil.example/x.png"/>',
            '<image xlink:href="http://evil.example/x"/>',
            '<image src="//evil.example/x"/>',
            '<rect style="fill: url(https://evil.example)"/>',
        )
        for body in cases:
            svg = f'<svg xmlns="http://www.w3.org/2000/svg">{body}</svg>'
            with self.subTest(body=body[:40]):
                self.assertEqual(self._refusal(svg).reason, "external-reference")

    def test_refuses_doctype_and_entities(self):
        for text in ('<!DOCTYPE svg [<!ENTITY x "y">]><svg/>',
                     '<!doctype svg><svg/>'):
            with self.subTest(text=text[:30]):
                self.assertEqual(self._refusal(text).reason, "declaration")

    def test_refuses_cdata(self):
        self.assertEqual(
            self._refusal('<svg xmlns="http://www.w3.org/2000/svg">'
                          '<![CDATA[x]]></svg>').reason,
            "active-content")

    def test_refuses_non_svg(self):
        for text in ("<html><body/></html>", "", "just words"):
            with self.subTest(text=text[:20]):
                self.assertEqual(self._refusal(text).reason, "wrong-shape")


class AntivirusIsRequiredTests(TestCase):
    """The coupling, checked rather than hoped for.

    Every other caller of ``toto.vault.scanning`` degrades quietly without the
    antivirus app. Sketch must not: it renders SVG inline in our origin and it
    deleted its own guard to use the shared one. A build that forgets the flag
    has to fail loudly, not draw hostile files quietly.
    """

    def test_the_check_passes_here_because_antivirus_is_installed(self):
        from toto.sketch.apps import _antivirus_is_required

        self.assertEqual(_antivirus_is_required(None), [])

    def test_the_check_fails_when_antivirus_is_absent(self):
        from unittest import mock

        from toto.sketch.apps import _antivirus_is_required

        with mock.patch("django.apps.apps.is_installed", return_value=False):
            errors = _antivirus_is_required(None)

        self.assertEqual([e.id for e in errors], ["sketch.E001"])


# MEDIA_ROOT, not the host's: this suite runs under zenobia's settings in the
# monorepo gate, where the real media dir is a root-owned docker bind mount and
# every file-creating test dies on PermissionError before it asserts anything.
@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="sketch-test-"))
class _SketchFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(
            site_name="Test", author="tests", publication_year=2026, active=True)
        cls.user = User.objects.create_user("artist", password="x")
        cls.other = User.objects.create_user("other", password="x")
        cls.own = make_svg(cls.user, title="own", text=SKETCH_AUTHORED)
        cls.public = make_svg(cls.other, title="shared", is_public=True,
                              text=UPLOADED_PLAIN)
        cls.foreign = make_svg(cls.other, title="private-foreign")

    def setUp(self):
        self.client.force_login(self.user)


class SketchLifecycleTests(_SketchFixture):

    def test_create_mints_an_svg_vault_file(self):
        response = self.client.post(reverse("sketch:create"),
                                    {"filename": "figura"})
        vf = VaultFile.objects.get(title="figura.svg")
        self.assertRedirects(response, reverse("sketch:edit", args=[vf.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(vf.file_type, "svg")
        self.assertEqual(vf.owner, self.user)
        self.assertEqual(_read_raw(vf), EMPTY_SVG)
        self.assertTrue(vf.content_hash)
        self.assertEqual(vf.file_size_bytes, len(EMPTY_SVG.encode()))

    def test_save_round_trips_bytes_verbatim(self):
        # Opaque content included: what the editor cannot edit it must not
        # change, and the server must not either.
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=UPLOADED_PLAIN, content_type="image/svg+xml")
        self.assertEqual(response.status_code, 200)
        self.own.refresh_from_db()
        self.assertEqual(_read_raw(self.own), UPLOADED_PLAIN)
        import hashlib
        self.assertEqual(self.own.content_hash,
                         hashlib.sha256(UPLOADED_PLAIN.encode()).hexdigest())

    def test_hostile_save_is_refused_and_changes_nothing(self):
        before = _read_raw(self.own)
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data='<svg xmlns="x"><script>alert(1)</script></svg>',
            content_type="image/svg+xml")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"], "active-content")
        self.assertEqual(_read_raw(self.own), before)

    def test_save_permissions_and_contract(self):
        url = reverse("sketch:save", args=[self.own.pk])
        self.assertEqual(self.client.get(url).status_code, 405)      # GET
        self.client.logout()
        # 401 is the APP's answer, and it is what runs under
        # toto.sketch.testing.settings. A host may refuse earlier and more
        # bluntly — zenobia carries LoginRequiredEverywhereMiddleware, which
        # 302s an anonymous request before any view is reached. Both are a
        # refusal; asserting only one made this test host-specific.
        self.assertIn(
            self.client.post(url, data=EMPTY_SVG,
                             content_type="image/svg+xml").status_code,
            (302, 401))
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(url, data=EMPTY_SVG,
                             content_type="image/svg+xml").status_code, 404)

    def test_save_size_cap(self):
        huge = EMPTY_SVG[:-7] + ("x" * SAVE_MAX_BYTES) + "</svg>"
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=huge, content_type="image/svg+xml")
        self.assertEqual(response.status_code, 413)

    def test_a_drawing_over_djangos_form_ceiling_still_saves(self):
        """The bug `_read_svg_body` exists for.

        `request.body` is checked against DATA_UPLOAD_MAX_MEMORY_SIZE, which
        this host leaves at Django's 2.5 MB default — so every drawing carrying
        a background image, which is exactly what the 10 MB cap was raised for,
        died as a bare 400 before the view ran. 4 MB is over Django's ceiling
        and well under ours.
        """
        big = EMPTY_SVG[:-7] + ("<!-- " + "x" * 4_000_000 + " -->") + "</svg>"
        self.assertGreater(len(big.encode()), 2_500_000)
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=big, content_type="image/svg+xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_delete_owner_only(self):
        # A per-test file: deleting the shared fixture's STORAGE file would
        # leak across tests (the DB rolls back, the filesystem does not).
        doomed = make_svg(self.user, title="doomed")
        self.client.force_login(self.other)
        response = self.client.post(reverse("sketch:delete", args=[doomed.pk]))
        self.assertEqual(response.status_code, 404)
        self.client.force_login(self.user)
        self.client.post(reverse("sketch:delete", args=[doomed.pk]))
        self.assertFalse(VaultFile.objects.filter(pk=doomed.pk).exists())


class SketchIndexRedirectTests(_SketchFixture):
    """`/sketch/` hands you to the vault.

    It was Office's Drawings tab until Office retired to limbo (2026-09-02),
    and this app's own flat list had already been given up to that hub. The
    vault is the listing that remains; `accessible_files` is still what
    decides, and ListingAgreementTests below owns that property.
    """

    def test_the_index_sends_you_to_the_vault(self):
        response = self.client.get(reverse("sketch:index"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("vault:root"))

    def test_the_index_is_not_a_write_route(self):
        """RedirectView answers every verb unless pinned; a POST must 405."""
        self.assertEqual(self.client.post(reverse("sketch:index")).status_code, 405)


class SketchEditViewTests(_SketchFixture):

    def test_owner_opens_editable(self):
        response = self.client.get(reverse("sketch:edit", args=[self.own.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "sketch-content")
        self.assertContains(response, "sketchEditor(")

    def test_public_file_opens_read_only(self):
        response = self.client.get(reverse("sketch:edit", args=[self.public.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_edit"])

    def test_foreign_private_404(self):
        response = self.client.get(reverse("sketch:edit", args=[self.foreign.pk]))
        self.assertEqual(response.status_code, 404)

    def test_hostile_file_is_refused_without_content(self):
        secret = "PAYLOAD-MARKER-42"
        hostile = make_svg(
            self.user, title="bad",
            text=f'<svg xmlns="x" onload="x()"><rect id="{secret}"/></svg>')
        response = self.client.get(reverse("sketch:edit", args=[hostile.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, secret)
        self.assertEqual(response.context["refusal"].reason, "active-content")
        # Owner gets the raw-XML way in.
        self.assertTrue(response.context["xml_url"])

    def test_the_xml_escape_is_owner_only(self):
        hostile = make_svg(
            self.other, title="bad-public", is_public=True,
            text='<svg xmlns="x" onload="x()"/>')
        response = self.client.get(reverse("sketch:edit", args=[hostile.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["xml_url"], "")


class VaultRoutingTests(_SketchFixture):
    """Pins the plugin-placement lesson: svg files open in Sketch."""

    def test_the_registry_routes_svg_to_sketch(self):
        plugin = VaultEditorPlugin.for_file_type("svg")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.get_editor_url(self.own),
                         reverse("sketch:edit", args=[self.own.pk]))


class SourceViewTests(_SketchFixture):
    """The XML view of a drawing, and applying an edit made there."""

    def test_get_hands_back_the_file_verbatim(self):
        response = self.client.get(reverse("sketch:source", args=[self.own.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), SKETCH_AUTHORED)

    def test_apply_screens_before_it_agrees(self):
        """The client parses; the server decides whether it may."""
        hostile = '<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'

        response = self.client.post(
            reverse("sketch:source", args=[self.own.pk]),
            data=hostile, content_type="image/svg+xml")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["reason"], "active-content")

    def test_apply_writes_nothing(self):
        """Applying seeds the board. Only Save touches the file."""
        good = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
                '<rect width="4" height="4"/></svg>')

        response = self.client.post(
            reverse("sketch:source", args=[self.own.pk]),
            data=good, content_type="image/svg+xml")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["svg"], good)
        self.assertEqual(_read_raw(self.own), SKETCH_AUTHORED)

    def test_a_reader_may_look_but_not_apply(self):
        self.client.force_login(self.other)

        self.assertEqual(
            self.client.get(reverse("sketch:source", args=[self.public.pk])).status_code,
            200)
        self.assertEqual(
            self.client.post(reverse("sketch:source", args=[self.own.pk]),
                             data="<svg/>", content_type="image/svg+xml").status_code,
            404)   # not even visible to them, let alone writable


class ConcurrencyTests(_SketchFixture):
    """Two tabs on one drawing. They used to overwrite each other in silence."""

    CLEAN = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>'

    def test_a_stale_base_hash_is_refused_and_the_work_is_kept(self):
        # Read the bytes back first rather than comparing to the fixture
        # constant: setUpTestData rolls the DATABASE back between tests, never
        # MEDIA_ROOT, so a sibling test's save is still on disk here.
        before = _read_raw(self.own)

        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=self.CLEAN, content_type="image/svg+xml",
            HTTP_X_BASE_HASH="0" * 64)

        self.assertEqual(response.status_code, 409)
        payload = response.json()
        # Refusing alone is what a user experiences as "it ate my drawing".
        self.assertIsNotNone(payload["kept_as_version"])
        self.assertEqual(_read_raw(self.own), before)

    def test_the_current_hash_saves_and_the_next_base_comes_back(self):
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=self.CLEAN, content_type="image/svg+xml",
            HTTP_X_BASE_HASH=self.own.content_hash)

        self.assertEqual(response.status_code, 200)
        self.own.refresh_from_db()
        self.assertEqual(response.json()["content_hash"], self.own.content_hash)
        self.assertEqual(_read_raw(self.own), self.CLEAN)

    def test_a_save_without_a_base_hash_still_works(self):
        """Old clients, and the very first save of a file with no hash yet."""
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=self.CLEAN, content_type="image/svg+xml")

        self.assertEqual(response.status_code, 200)

    def test_someone_elses_lock_gets_423_not_409(self):
        """A retry cannot succeed until they leave, so inviting one would lie."""
        from toto.vault import locks

        locks.acquire(self.own, self.other)
        before = _read_raw(self.own)

        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=self.CLEAN, content_type="image/svg+xml")

        self.assertEqual(response.status_code, 423)
        self.assertEqual(response.json()["locked_by"], self.other.username)
        self.assertEqual(_read_raw(self.own), before)


class ScanRecordTests(_SketchFixture):
    """A drawing saved through sketch is a scanned file like any other."""

    def test_saving_records_a_clean_verdict_and_earns_the_tick(self):
        from toto.vault.scanning import clean_file_ids

        clean = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="2" height="2"/></svg>'
        self.client.post(reverse("sketch:save", args=[self.own.pk]),
                         data=clean, content_type="image/svg+xml")

        self.own.refresh_from_db()
        self.assertEqual(clean_file_ids([self.own]), {self.own.pk})


class StyledTextTests(TestCase):
    """Text carries a font, a weight, a slant, an underline and letter spacing.

    Two things matter here and neither is cosmetic. A drawing is an .svg FILE
    the platform renders and the scanner screens, so the font family comes from
    a fixed allow-list rather than a text field — a typed family is markup, and
    the request after that is @font-face, which is a URL out of a document we
    promise is inert. And the styling has to survive a save: the emitter and
    the parser are two halves of one contract, and a round trip that drops
    formatting is the bug somebody notices a week later on a finished diagram.
    """

    def _model_js(self):
        import pathlib

        return (pathlib.Path(__file__).parent / "static" / "sketch"
                / "model.js").read_text()

    def _svgdoc_js(self):
        import pathlib

        return (pathlib.Path(__file__).parent / "static" / "sketch"
                / "svgdoc.js").read_text()

    def test_a_styled_label_passes_the_scanner(self):
        """Everything the emitter can now write, screened as the file it is."""
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<text x="10" y="20" fill="#000000" opacity="1" font-size="24"'
            ' font-family="Georgia, \'Times New Roman\', serif"'
            ' font-weight="bold" font-style="italic"'
            ' text-decoration="underline" letter-spacing="2">Hello</text>'
            "</svg>"
        )

        verdict = scan(svg, file_type="svg")

        self.assertTrue(verdict.ok, f"{verdict.reason}: {verdict.detail}")

    def test_the_font_list_names_no_external_source(self):
        """Every stack must resolve to fonts already on the machine. A url() or
        an @font-face here would put a network fetch in a drawing."""
        model = self._model_js()
        start = model.index("var FONT_CHOICES")
        block = model[start:model.index("var DEFAULT_FONT")]

        for forbidden in ("url(", "@font-face", "http://", "https://", "//fonts"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, block)

    def test_the_emitter_writes_attributes_not_a_style_string(self):
        """The scanner reads attributes; a style="" is a place to hide a
        declaration it would have to parse CSS to see."""
        model = self._model_js()
        # model.js holds TWO `case "text"` blocks — scaleShape's and the
        # emitter's. Anchored on the markup only the emitter writes.
        start = model.index(chr(39) + '<text x="' + chr(39))
        block = model[start:start + 900]

        self.assertIn('font-weight="', block)
        self.assertIn('letter-spacing="', block)
        self.assertNotIn(' style="', block)

    def test_the_parser_reads_back_everything_the_emitter_writes(self):
        """Two halves of one contract. A save-and-reload that drops these
        silently flattens every label in the file to plain sans."""
        parser = self._svgdoc_js()
        start = parser.index('case "text": {')
        block = parser[start:start + 2200]

        for attribute in ("font-family", "font-weight", "font-style",
                          "text-decoration", "letter-spacing"):
            with self.subTest(attribute=attribute):
                self.assertIn(attribute, block)

    def test_an_unknown_family_falls_back_rather_than_passing_through(self):
        """An .svg may arrive from anywhere. A family we do not recognise must
        not be carried into what we re-emit."""
        parser = self._svgdoc_js()
        start = parser.index('case "text": {')
        block = parser[start:start + 2200]

        self.assertIn("M.DEFAULT_FONT", block)
        self.assertIn("M.FONT_CHOICES", block)

    def test_measurement_accounts_for_the_styling(self):
        """Bold, a condensed family and tracking all change advance width, and
        a box measured without them leaves the selection outline wrong."""
        import pathlib

        editor = (pathlib.Path(__file__).parent / "static" / "sketch"
                  / "editor.js").read_text()
        start = editor.index("function measure(t, size, style)")
        block = editor[start:start + 900]

        self.assertIn("fontShorthand", block)
        self.assertIn("tracking", block)

    def test_tracking_goes_both_ways(self):
        """Tightening a heading is the common reason to touch this at all."""
        model = self._model_js()
        start = model.index("var TRACKING_STEPS")
        block = model[start:model.index("function fontStack")]

        self.assertIn("-1", block)
        self.assertIn("0,", block)


    def test_letter_spacing_scales_with_the_type(self):
        """A length in user units. Left fixed, an enlarged label looks
        progressively tighter — the one thing tracking should hold steady."""
        model = self._model_js()
        start = model.index('case "text": {')
        block = model[start:start + 700]

        self.assertIn("tracking", block)


class AssistantTests(_SketchFixture):
    """The first wiring: a drawing gets an add-element door, and nothing else.

    No Full rewrite (the source view's Apply is the one door for replacing a
    drawing) and no floating selection button (a drawing's selection is
    shapes, not text) — the surface offers exactly one write: append new
    shapes, screened server-side as SVG before anyone is offered them.
    """

    def test_the_surface_is_declared_and_screened_as_svg(self):
        import toto.sketch.ai_surfaces  # noqa: F401 — idempotent registration
        from toto.core.ai_surfaces import ELEMENT_ACTION, registry

        surface = registry.get("sketch")

        self.assertEqual(surface.file_type, "svg")
        element = surface.action(ELEMENT_ACTION)
        self.assertIsNotNone(element)
        self.assertIn("<svg>", element.system)
        self.assertIn("No scripts", element.system)
        self.assertIsNotNone(surface.action("ask"))

    def test_the_owner_gets_the_surface_and_a_reader_does_not(self):
        from django.apps import apps

        import toto.sketch.ai_surfaces  # noqa: F401

        expected = "sketch" if apps.is_installed("toto.steven") else ""

        own = self.client.get(reverse("sketch:edit", args=[self.own.pk]))
        shared = self.client.get(reverse("sketch:edit", args=[self.public.pk]))

        self.assertEqual(own.context["steven_surface"], expected)
        # can_edit is False on a shared file, so no button — the assistant
        # only writes where the human can.
        self.assertEqual(shared.context["steven_surface"], "")

    def test_the_page_and_the_engine_carry_the_wiring(self):
        import pathlib

        base = pathlib.Path(__file__).parent
        edit = (base / "templates" / "sketch" / "edit.html").read_text()
        self.assertIn("steven/_ai.html", edit)
        self.assertIn("steven/_head.html", edit)
        # The page always used x-cloak and never carried the rule; the
        # assistant's modal made that a visible flash.
        self.assertIn("[x-cloak]{display:none!important}", edit)

        js = (base / "static" / "sketch" / "editor.js").read_text()
        self.assertIn('StevenActions.register("sketch"', js)
        self.assertIn("engine.addShapes", js)
        # The registration offers insert and never writeDocument — no Full
        # rewrite of a drawing, deliberately. (Sliced to the registration
        # object: the comments around it are allowed to SAY writeDocument.)
        registration = js[js.index('register("sketch"'):]
        registration = registration[:registration.index("});")]
        self.assertIn("insert:", registration)
        self.assertNotIn("writeDocument:", registration)


class CsrfTests(_SketchFixture):
    """Both write endpoints are CSRF-protected, and one of them never was.

    `sketch_save` carried @csrf_exempt, which hid the client's missing token.
    `sketch_source` did not — so "Apply to the board" answered 403 in every real
    browser since it was written, and the tests never saw it because Django's
    test client runs with enforce_csrf_checks=False.
    """

    def _csrf_client(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        return client

    def test_save_without_a_token_is_refused(self):
        client = self._csrf_client()
        response = client.post(reverse("sketch:save", args=[self.own.pk]),
                               data=EMPTY_SVG, content_type="image/svg+xml")
        self.assertEqual(response.status_code, 403)

    def test_apply_from_source_without_a_token_is_refused(self):
        client = self._csrf_client()
        response = client.post(reverse("sketch:source", args=[self.own.pk]),
                               data=EMPTY_SVG, content_type="image/svg+xml")
        self.assertEqual(response.status_code, 403)

    def test_the_client_sends_the_token_on_both_posts(self):
        """The other half: the server demanding a token is only useful if the
        editor sends one. Asserted against the shipped asset, the way the
        assistant wiring is."""
        import pathlib
        js = (pathlib.Path(__file__).parent / "static" / "sketch" / "editor.js").read_text()
        self.assertEqual(js.count('"X-CSRFToken": csrf()'), 2)
        self.assertIn('"X-Base-Hash": baseHash', js)


class ListingAgreementTests(_SketchFixture):
    """What a listing offers, this view opens.

    `_get_readable_file` restated the vault's permission rule with two of its
    five clauses, so a bucket owner or a shared-directory member saw a drawing
    listed (in Office's Drawings tab then; in the vault now) and got a 404 on
    click. The list and the page have to agree by construction, which means
    asking `access.may_read` and not re-deriving it.
    """

    def test_a_bucket_owner_who_is_not_the_file_owner_can_open_it(self):
        from toto.vault.models import Bucket
        bucket = Bucket.objects.create(name="shared-bucket", owner=self.user)
        drawing = make_svg(self.other, title="in-my-bucket")
        VaultFile.objects.filter(pk=drawing.pk).update(bucket=bucket)
        response = self.client.get(reverse("sketch:edit", args=[drawing.pk]))
        self.assertEqual(response.status_code, 200)
        # Readable, but not writable: every write route is owner-only.
        self.assertFalse(response.context["can_edit"])

    def test_everything_the_listing_offers_actually_opens(self):
        from toto.vault.filetree import accessible_files
        for vault_file in accessible_files(self.user, file_types=("svg",)):
            with self.subTest(title=vault_file.title):
                response = self.client.get(
                    reverse("sketch:edit", args=[vault_file.pk]))
                self.assertEqual(response.status_code, 200)


class BlankDrawingTests(_SketchFixture):
    """A new drawing is sketch's board, not the vault's 100x100 stub."""

    def test_the_plugin_declares_an_extension(self):
        plugin = VaultEditorPlugin.for_file_type("svg")
        self.assertEqual(plugin.new_file_extension, ".svg")

    def test_blank_content_is_the_drawing_board(self):
        plugin = VaultEditorPlugin.for_file_type("svg")
        blank = plugin.blank_content("untitled.svg")
        self.assertEqual(blank, EMPTY_SVG)
        self.assertIn('viewBox="0 0 1920 1080"', blank)

    def test_a_new_drawing_is_seeded_from_the_plugin(self):
        """The precedence flip. The vault also has an `svg` stub — a 100x100
        board on which sketch's default stroke widths and text sizes are
        absurd — and it used to win, so every new drawing opened broken."""
        from toto.vault.models import Bucket
        from toto.vault.views import create_empty_vault_file
        bucket = Bucket.objects.create(name="b", owner=self.user)
        vault_file = create_empty_vault_file(
            self.user, bucket, None, "fresh.svg", "svg")
        self.assertEqual(_read_raw(vault_file), EMPTY_SVG)

    def test_a_type_the_vault_seeds_itself_is_untouched(self):
        """The `or None` guard: the base `blank_content` returns "", and "" is
        a legitimate blank for text and friends. Without the guard every
        plugin-having type would be seeded empty."""
        from toto.vault.views import CreateEmptyFileView
        self.assertTrue(CreateEmptyFileView._INITIAL.get("latex"))


class MeteringTests(_SketchFixture):
    """A save keeps a version and counts itself."""

    def test_a_save_keeps_a_version_and_records_usage(self):
        from toto.sketch.models import SketchUsageEvent
        from toto.vault import versions
        before = len(versions.list_versions(self.own))
        response = self.client.post(
            reverse("sketch:save", args=[self.own.pk]),
            data=SKETCH_AUTHORED.replace("</svg>", "<rect x='1' y='1' "
                                         "width='2' height='2'/></svg>"),
            content_type="image/svg+xml")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(versions.list_versions(self.own)), before + 1)
        self.assertTrue(
            SketchUsageEvent.objects.filter(user=self.user,
                                            metric_code="sketch.save").exists())
