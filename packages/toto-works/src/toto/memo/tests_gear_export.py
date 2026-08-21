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
from unittest import mock

from toto.memo import render_pdf


class GearSelectionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("deck", password="x")

    def test_no_anastasia_means_render_here(self):
        """Irena's world: the app is not installed, so there is nothing to
        ask, and the local renderer is the only path."""
        with mock.patch("django.apps.apps.is_installed", return_value=False):
            self.assertIsNone(render_pdf.gear_for(self.user))

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
