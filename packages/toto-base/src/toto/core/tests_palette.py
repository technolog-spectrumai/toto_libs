"""The one colour palette (2026-10-10): a file, not a database row.

``palette.json`` beside ``toto/core/palette.py`` is the only place a colour
is written. The build compiles it into the stylesheet
(``tailwind/tailwind.config.js``), and a page reads it through the module:
the colour map handed to charts as JSON, the Play CDN's inline configuration
on a checkout without the build, the header's classes. No host carries a
theme file, and nothing of the palette is seeded.
"""

import json
import re
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, TestCase

from toto.core import palette

CORE = Path(palette.__file__).resolve().parent

#: The tokens the templates name, as the ColorMix model carried them: fifteen
#: in each mode and the two accents.
MODED = ("primary-bg", "header-bg", "appbar-bg", "appbar-text", "footer-bg",
         "footer-text", "bubble-bg", "text-main", "accent", "warn", "success",
         "sunken", "link", "security", "caution")
TOKENS = {f"{token}-{mode}" for token in MODED for mode in ("light", "dark")} | {
    "accent-1", "accent-2"}


class PaletteFileTests(SimpleTestCase):
    def setUp(self):
        palette.palette.cache_clear()
        self.addCleanup(palette.palette.cache_clear)

    def test_the_file_carries_every_token_and_nothing_else(self):
        self.assertEqual(set(palette.colors()), TOKENS)

    def test_every_colour_is_a_hex_tailwind_can_compile(self):
        for token, value in palette.colors().items():
            with self.subTest(token=token):
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$")

    def test_security_and_caution_are_cyan_and_amber_tokens_of_their_own(self):
        colors = palette.colors()
        for key in ("security-light", "security-dark", "caution-light", "caution-dark"):
            self.assertIn(key, colors)
        self.assertNotEqual(colors["security-light"], colors["accent-light"])

    def test_the_shape_pages_are_handed(self):
        theme = palette.theme()
        self.assertEqual(theme["theme"]["colors"], palette.colors())
        self.assertEqual(set(theme["header"]), {"light", "dark"})
        self.assertEqual(theme["name"], palette.name())
        self.assertTrue(palette.font_name())

    def test_the_colours_handed_out_are_a_copy(self):
        palette.colors()["accent-light"] = "#000000"
        self.assertNotEqual(palette.colors()["accent-light"], "#000000")

    def test_a_palette_that_is_not_one_is_refused_in_words(self):
        good = json.loads(palette.PALETTE_FILE.read_text(encoding="utf-8"))
        for change, said in (
                ({"colors": {"accent-light": "red"}}, "not a hex colour"),
                ({"colors": {"accent-light": "#11223380"}}, "not a hex colour"),
                ({"colors": {"x;}body{": "#000000"}}, "not a colour token"),
                ({"colors": {}}, "no 'colors'"),
                ({"font": ""}, "no 'font'"),
                ({"header": {"top": "x"}}, "'header'")):
            with self.subTest(change=change), self.assertRaises(palette.PaletteError) as caught:
                palette.check({**good, **change})
            self.assertIn(said, str(caught.exception))

    def test_a_missing_or_broken_file_is_a_palette_error(self):
        with mock.patch.object(palette, "PALETTE_FILE", CORE / "no-such-palette.json"), \
                self.assertRaises(palette.PaletteError):
            palette.palette()


class OnePaletteTests(SimpleTestCase):
    """Nothing beside the file states a colour of the palette."""

    def test_the_tailwind_config_reads_the_file_and_names_no_colour(self):
        config = (CORE / "tailwind" / "tailwind.config.js").read_text(encoding="utf-8")
        self.assertIn('require("../palette.json")', config)
        self.assertIn("colors: palette.colors", config)
        self.assertIn('sans: ["var(--oya-font-sans)"]', config)
        self.assertIn("content: []", config)
        code = "\n".join(line for line in config.splitlines()
                         if not line.lstrip().startswith("//"))
        self.assertNotRegex(code, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotIn("--oya-${token}", code)
        self.assertNotIn("darkMode", code)

    def test_the_safelist_is_the_hosts_and_optional(self):
        config = (CORE / "tailwind" / "tailwind.config.js").read_text(encoding="utf-8")
        self.assertIn("process.env.TOTO_TAILWIND_SAFELIST", config)

    def test_the_models_carry_no_colour(self):
        from toto.core import models

        self.assertFalse(hasattr(models, "ColorMix"))
        self.assertFalse(hasattr(models, "Theme"))
        self.assertEqual(models.Platform._meta.get_field("font").related_model, models.Font)
        self.assertNotIn("theme", {f.name for f in models.Platform._meta.get_fields()})

    def test_every_colour_token_a_core_template_names_is_in_the_palette(self):
        """A class on a token the palette lacks renders colourless, which is
        how `caution` sat unstyled for months. Read loosely: any colour
        utility ending in -light or -dark, or on accent-1/2. A match that is
        itself a token (the key "text-main-dark" in a script) is one too."""
        colors = set(palette.colors())
        pattern = re.compile(
            r"(?<![\w-])((?:bg|text|border|ring|fill|stroke|from|to|via|divide|outline|"
            r"decoration|placeholder|caret)-([a-z]+(?:-[a-z]+)*-(?:light|dark)|accent-[12]))"
            r"(?![\w-])")
        named, missing = 0, set()
        for path in (CORE / "templates").rglob("*.html"):
            for whole, token in pattern.findall(path.read_text(encoding="utf-8")):
                named += 1
                if token not in colors and whole not in colors:
                    missing.add(whole)
        self.assertGreater(named, 50, "vacuous")
        self.assertEqual(missing, set())


class PlatformThemeTests(TestCase):
    """What the page context carries: the palette, and the platform's font."""

    def test_a_platform_is_serialized_with_the_palette_and_its_font(self):
        from toto.core.models import Font, Platform
        from toto.core.serializers import PlatformSerializer

        font = Font.objects.create(name="Orbitron", cdn_link="https://fonts.example/o.css")
        platform = Platform.objects.create(site_name="P", author="t", publication_year=2026,
                                           active=True, font=font)
        theme = PlatformSerializer(platform).data["theme"]
        self.assertEqual(theme["theme"]["colors"], palette.colors())
        self.assertEqual(theme["header"], palette.header())
        self.assertEqual((theme["font"]["name"], theme["font"]["style_family"]),
                         ("Orbitron", "sans-serif"))

    def test_a_platform_without_a_font_still_has_the_colours(self):
        from toto.core.models import Platform
        from toto.core.serializers import PlatformSerializer

        platform = Platform.objects.create(site_name="P", author="t", publication_year=2026,
                                           active=True)
        theme = PlatformSerializer(platform).data["theme"]
        self.assertIsNone(theme["font"])
        self.assertEqual(theme["theme"]["colors"], palette.colors())
