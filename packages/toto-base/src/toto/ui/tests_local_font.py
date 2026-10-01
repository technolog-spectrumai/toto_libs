"""The theme font from the platform itself (2026-10-01, 37c.20).

Every seeded font is a Google Fonts stylesheet, so with USE_EXTERNAL_FONTS on
(the library's default) each page sends the visitor's address and browser to
Google before it has said a word — the welcome, sign-in and privacy pages
included. A host that turns it off (zenobia) now gets the theme font from the
platform's own copy instead: `toto.ui.page.LOCAL_FONTS`, a woff2 file beside
its licence under `oya/fonts/`, drawn by oya/base.html as an @font-face. A
font without a copy falls back to its style family, and asks nobody.
"""

from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from toto.core.models import ColorMix, Font, Platform, Theme
from toto.ui.page import LOCAL_FONTS, PageProcessor, local_font

#: The seeded link (zenobia/data/fonts.json).
GOOGLE = "https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&display=swap"
GOOGLE_HOSTS = ("fonts.googleapis.com", "fonts.gstatic.com")


class ShippedFontTests(SimpleTestCase):
    def test_orbitron_is_shipped_with_its_licence(self):
        path = finders.find(LOCAL_FONTS["Orbitron"]["file"])
        self.assertTrue(path, "the woff2 file is not among the static files")
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(4), b"wOF2")
        licence = finders.find("oya/fonts/orbitron/OFL.txt")
        self.assertTrue(licence, "the font's licence must ship beside it")
        with open(licence, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("SIL OPEN FONT LICENSE Version 1.1", text)
        self.assertIn('Reserved Font Name: "Orbitron"', text)

    def test_every_local_font_is_a_woff2_file_the_finders_reach(self):
        for name, entry in LOCAL_FONTS.items():
            with self.subTest(font=name):
                self.assertTrue(entry["file"].endswith(".woff2"))
                self.assertTrue(finders.find(entry["file"]))

    def test_the_local_copy_names_its_static_url_and_its_weights(self):
        found = local_font("Orbitron")
        self.assertTrue(found["url"].endswith("oya/fonts/orbitron/orbitron-latin.woff2"))
        self.assertEqual(found["weight"], "400 900")

    def test_a_font_the_platform_does_not_ship_has_no_local_copy(self):
        for name in ("Inter", "", None):
            with self.subTest(font=name):
                self.assertIsNone(local_font(name))


class BaseTemplateFontTests(TestCase):
    """oya/base.html, rendered with the context every page gets."""

    def setUp(self):
        self.font = Font.objects.create(name="Orbitron", cdn_link=GOOGLE,
                                        style_family="sans-serif")
        self.theme = Theme.objects.create(
            name="Amazing Moon", color_mix=ColorMix.objects.create(name="Mix"),
            font=self.font)
        Platform.objects.create(site_name="Zenobia", author="Tests",
                                publication_year=2026, active=True, theme=self.theme)
        self.request = RequestFactory().get("/")
        self.request.user = AnonymousUser()

    def page(self):
        context = PageProcessor().decorate({}, self.request)
        return context, render_to_string("oya/base.html", context, request=self.request)

    @override_settings(USE_EXTERNAL_FONTS=False)
    def test_without_external_fonts_the_page_draws_the_platforms_own_copy(self):
        context, html = self.page()
        for host in GOOGLE_HOSTS:
            self.assertNotIn(host, html)
        self.assertIn("@font-face", html)
        self.assertIn("font-family: 'Orbitron';", html)
        self.assertIn("font-weight: 400 900;", html)
        self.assertIn(f'src: url("{context["local_font"]["url"]}") format("woff2");', html)

    @override_settings(USE_EXTERNAL_FONTS=True)
    def test_with_external_fonts_the_fonts_own_stylesheet_is_linked(self):
        context, html = self.page()
        self.assertIsNone(context["local_font"])
        self.assertIn('<link href="https://fonts.googleapis.com/css2?family=Orbitron', html)
        self.assertNotIn("@font-face", html)

    @override_settings(USE_EXTERNAL_FONTS=False)
    def test_a_font_without_a_local_copy_falls_back_and_asks_nobody(self):
        Font.objects.filter(pk=self.font.pk).update(
            name="Inter", cdn_link="https://fonts.googleapis.com/css2?family=Inter")
        context, html = self.page()
        self.assertIsNone(context["local_font"])
        for host in GOOGLE_HOSTS:
            self.assertNotIn(host, html)
        self.assertNotIn("@font-face", html)

    @override_settings(USE_EXTERNAL_FONTS=False)
    def test_a_theme_without_a_font_is_no_error(self):
        Theme.objects.filter(pk=self.theme.pk).update(font=None)
        context, html = self.page()
        self.assertIsNone(context["local_font"])
        self.assertNotIn("@font-face", html)
