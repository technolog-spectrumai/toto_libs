"""Where a deck export renders, and why the answer differs per host.

`toto.memo` ships in toto-works, which BOTH Zenobia and Irena pin — and they
made opposite choices. Zenobia carries no PDF library at all and exports in an
anastasia-pdf runner; Irena deliberately has no Compute Gears and renders in
the request. One module has to serve both, so the selection is a function
rather than a build-time decision, and this file pins both branches.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from unittest import mock, skipUnless

from django.apps import apps as django_apps

from toto.memo import render_pdf


class GearSelectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("deck", password="x")

    def test_no_anastasia_means_render_here(self):
        """Irena's world: the app is not installed, so there is nothing to
        ask, and the local renderer is the only path."""
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertIsNone(render_pdf.gear_for(self.user))

    @skipUnless(django_apps.is_installed("toto.anastasia"),
                "this host runs no Compute Gears")
    @override_settings(ANASTASIA_POOL={"cpu_millicores": 4000, "ram_mb": 8192,
                                       "scratch_mb": 8192, "pids": 2048})
    def test_a_mounted_gear_is_used_when_there_is_one(self):
        from toto.anastasia import services
        from toto.anastasia.limits import Limits

        with override_settings(ANASTASIA_RUNTIME_BACKEND=
                               "toto.anastasia.tests.base.FakeRuntimeBackend"):
            lease = services.reserve(owner=self.user, name="decks",
                                     limits=Limits(1000, 1024, 512, 128))
            services.mount(lease=lease, actor=self.user)
            self.assertEqual(render_pdf.gear_for(self.user).pk, lease.pk)

    @skipUnless(django_apps.is_installed("toto.anastasia"),
                "this host runs no Compute Gears")
    @override_settings(ANASTASIA_POOL={"cpu_millicores": 4000, "ram_mb": 8192,
                                       "scratch_mb": 8192, "pids": 2048})
    def test_no_gear_falls_back_only_if_this_host_can_render(self):
        """The asymmetry that keeps both hosts working: a host WITH the library
        quietly renders here; a host without it must say so, because otherwise
        the user gets a 500 for a fixable situation."""
        with mock.patch.object(render_pdf, "is_available", return_value=True):
            self.assertIsNone(render_pdf.gear_for(self.user))

        from toto.anastasia import jobs

        with mock.patch.object(render_pdf, "is_available", return_value=False):
            with self.assertRaises(jobs.NoGear):
                render_pdf.gear_for(self.user)


class KatexTravelTests(TestCase):
    def test_the_fonts_travel_with_the_document(self):
        """katex.min.css asks for its fonts with a relative url(fonts/…). On
        this host that resolved against a directory; in a runner they have to
        be staged, or every formula is a row of blank boxes."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            katex = Path(tmp)
            fonts = katex / "fonts"
            fonts.mkdir()
            (fonts / "KaTeX_Main-Regular.woff2").write_bytes(b"woff2")
            (fonts / "KaTeX_Main-Regular.ttf").write_bytes(b"ttf")
            (fonts / "KaTeX_Main-Regular.woff").write_bytes(b"woff")

            staged = render_pdf.katex_inputs(katex)

        # woff2 only: three formats of every face would triple the payload for
        # a renderer that takes the first one it can read.
        self.assertEqual(list(staged), ["fonts/KaTeX_Main-Regular.woff2"])

    def test_no_katex_is_not_an_error(self):
        """A machine where download_vendor.py has not run simply has none, and
        formulas fall back to their LaTeX source."""
        self.assertEqual(render_pdf.katex_inputs(None), {})


class SeededDecksAreLightTests(TestCase):
    """The samples ship LIGHT, though the format default is "black".

    A sample deck is the first thing anybody opens and the thing they copy to
    start their own — and a dark deck exported to PDF prints a full-bleed black
    page. See `ingress_memo.SAMPLE_THEME`.
    """

    def test_every_sample_is_light(self):
        from toto.memo import presentation_format as fmt
        from toto.memo.management.commands.ingress_memo import build

        for name, deck in build(fmt):
            with self.subTest(deck=name):
                self.assertEqual(deck.theme, "white")

    def test_every_sample_actually_exports(self):
        """The samples are the one deck set we can pin. If these stop
        exporting, the exporter broke — not somebody's deck."""
        from toto.memo import presentation_format as fmt, render_pdf
        from toto.memo.management.commands.ingress_memo import build

        if not render_pdf.is_available():
            self.skipTest("WeasyPrint is not installed")
        for name, deck in build(fmt):
            with self.subTest(deck=name):
                self.assertTrue(render_pdf.render(deck).startswith(b"%PDF"))


class CulpritSlideTests(TestCase):
    """A failed export should name the slide, not point at the server log."""

    def _deck(self):
        from toto.memo import presentation_format as fmt

        def slide(title):
            return fmt.Slide(
                id=fmt._new_id("s"), title=title, layout="title-content",
                blocks=[fmt.Block(id=fmt._new_id("b"), type="text",
                                  payload=title, slot="")])

        return fmt.Presentation(title="D", slides=[slide("a"), slide("b")])

    def test_a_deck_that_renders_has_no_culprit(self):
        from toto.memo import render_pdf
        from toto.memo.views import _first_unrenderable_slide

        if not render_pdf.is_available():
            self.skipTest("WeasyPrint is not installed")
        self.assertIsNone(_first_unrenderable_slide(self._deck()))

    def test_it_returns_the_first_failing_slide_number(self):
        from unittest import mock

        from toto.memo import render_pdf
        from toto.memo.views import _first_unrenderable_slide

        if not render_pdf.is_available():
            self.skipTest("WeasyPrint is not installed")
        deck = self._deck()
        real = render_pdf.render

        def fake(presentation):
            if presentation.slides and presentation.slides[0].title == "b":
                # The exact shape reported from the field.
                raise TypeError(
                    "unsupported operand type(s) for *: 'NoneType' and 'int'")
            return real(presentation)

        with mock.patch.object(render_pdf, "render", fake):
            self.assertEqual(_first_unrenderable_slide(deck), 2)
