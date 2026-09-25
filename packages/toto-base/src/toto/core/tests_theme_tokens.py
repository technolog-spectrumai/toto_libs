"""Every colour a template names must be a token the theme model carries.

A key present only in a theme JSON is dropped by ``create_theme`` and the
class it produces renders colourless — which is how ``caution`` sat unstyled
for months. The model is the contract; this pins the two tokens added on
2026-09-25 and the rule that the JSON and the model agree.
"""

from django.test import TestCase

from toto.core.models import ColorMix, Theme


class ThemeTokenTests(TestCase):
    def test_security_and_caution_reach_the_tailwind_palette(self):
        mix = ColorMix.objects.create(name="t")
        theme = Theme.objects.create(name="t", color_mix=mix)
        colors = theme.theme["colors"]
        for key in ("security-light", "security-dark",
                    "caution-light", "caution-dark"):
            with self.subTest(key=key):
                self.assertIn(key, colors)
                self.assertTrue(colors[key].startswith("#"))

    def test_the_defaults_are_cyan_and_amber(self):
        mix = ColorMix.objects.create(name="d")
        self.assertEqual(mix.security_light.upper(), "#00ACC1")
        self.assertEqual(mix.security_dark.upper(), "#26C6DA")
        self.assertEqual(mix.caution_light.upper(), "#A07800")

    def test_a_theme_file_may_state_them(self):
        from django.core.management import call_command
        from toto.core.management.commands.create_theme import Command

        mix = Command().get_or_create_color_mix(
            "from-file", {"security-light": "#22b8cf", "caution-dark": "#f0a820",
                          "not-a-token": "#000000"})
        self.assertEqual(mix.security_light, "#22b8cf")
        self.assertEqual(mix.caution_dark, "#f0a820")
        self.assertFalse(hasattr(mix, "not_a_token"))
