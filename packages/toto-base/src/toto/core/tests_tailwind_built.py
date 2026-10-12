"""Tailwind built ahead of time, or the Play CDN where it was not (37c.15).

A host that builds ``oya/tailwind.css`` (zenobia's Dockerfile) gets a page
that links it; the colours are compiled into it from the one palette
(2026-10-10, tests_palette.py), so the page writes only the font as a CSS
variable. A checkout without it keeps the 409 KB script that compiles in the
browser (templatetags/oya_tailwind.py).
"""

from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.template.loader import get_template
from django.test import RequestFactory, SimpleTestCase

from toto.core.templatetags import oya_tailwind

THEME = {"theme": {"colors": {"accent-light": "#FF4081", "warn-dark": "#f52",
                              "sunken-light": "#11223380"}}}
FONT = {"name": "Orbitron", "style_family": "sans-serif"}


class ColourTests(SimpleTestCase):
    def test_the_page_writes_no_colour_variables(self):
        """They were `--oya-<token>: R G B`, from a database row, on every
        page; the stylesheet has the colours compiled in now."""
        self.assertFalse(hasattr(oya_tailwind, "theme_color_vars"))
        self.assertFalse(hasattr(oya_tailwind, "rgb_channels"))


class PageTests(SimpleTestCase):
    def setUp(self):
        oya_tailwind.built_css_present.cache_clear()
        self.addCleanup(oya_tailwind.built_css_present.cache_clear)

    def _render(self, name, built):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        with mock.patch.object(oya_tailwind, "built_css_present", return_value=built):
            return get_template(name).render({"theme": THEME, "font": FONT}, request)

    def test_a_checkout_without_the_build_compiles_in_the_browser(self):
        page = self._render("oya/maintenance.html", built=False)
        self.assertIn("oya/tailwind.js", page)
        self.assertNotIn("oya/tailwind.css", page)

    def test_a_built_host_links_the_stylesheet_and_no_script(self):
        page = self._render("oya/maintenance.html", built=True)
        self.assertIn("oya/tailwind.css", page)
        self.assertNotIn("oya/tailwind.js", page)

    def test_the_base_template_takes_the_same_turn(self):
        """Read, not rendered: the header and the plugins want a platform."""
        from pathlib import Path

        source = Path(get_template("oya/base.html").origin.name).read_text(encoding="utf-8")
        head = source[:source.index("</head>")]
        script = head.index("{% static 'oya/tailwind.js' %}")
        self.assertLess(head.index("{% if not tailwind_css %}"), script)
        built = head[head.index("{% if tailwind_css %}"):]
        self.assertIn("{% static 'oya/tailwind.css' %}", built)
        self.assertNotIn("theme_color_vars", source)
        self.assertIn(":root { --oya-font-sans:", built)
        self.assertEqual(built.count("--oya-"), 1)
        self.assertNotIn("tailwind.js", built)
        # The inline config only where the script reads it.
        config = source.index("tailwind.config = {")
        self.assertGreater(config, source.index("{% if not tailwind_css %}", script))

    def test_the_stylesheet_comes_last_in_the_head_as_the_scripts_style_did(self):
        """The Play CDN script appended its <style> to <head> once the body
        was there, after every stylesheet and <style> of the head; the built
        one keeps that place, so the cascade is the one the pages had."""
        from pathlib import Path

        for name in ("oya/base.html", "oya/maintenance.html"):
            with self.subTest(template=name):
                source = Path(get_template(name).origin.name).read_text(encoding="utf-8")
                head = source[:source.index("</head>")]
                after = head[head.index("{% static 'oya/tailwind.css' %}"):]
                for later in ("<link", "<script", "{% block"):
                    self.assertNotIn(later, after)
                self.assertNotIn("<style", after.replace('<style id="oya-theme">', ""))
        page = self._render("oya/maintenance.html", built=True)
        self.assertGreater(page.index("oya/tailwind.css"), page.rindex("</style>", 0, page.index("</head>")))

    def test_the_finder_answers_once(self):
        with mock.patch.object(oya_tailwind.finders, "find", return_value=None) as find:
            self.assertFalse(oya_tailwind.built_css_present())
            self.assertFalse(oya_tailwind.built_css_present())
        find.assert_called_once_with("oya/tailwind.css")
