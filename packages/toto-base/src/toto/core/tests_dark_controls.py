"""The browser's own controls follow the page's dark mode (2026-09-28).

Split out of ``tests_map_tiles`` on 2026-10-04, when the map tiles left
toto-base with ``toto.locations`` (that module is toto.locations' now).
"""

from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase


class DarkControlsTests(SimpleTestCase):
    def test_native_controls_follow_dark_mode(self):
        """`color-scheme` follows darkMode on <html>, so a field Django rendered
        without classes is not light text in a white box on a dark page (the
        forum cleanup form, 2026-09-28)."""
        source = Path(get_template("oya/base.html").origin.name).read_text(encoding="utf-8")
        self.assertIn(""":style="darkMode ? 'color-scheme: dark' : 'color-scheme: light'""", source)

    def test_the_base_template_draws_no_map_tiles(self):
        """No tile helper and no outside tile source on every page: the map
        is toto.locations' own (toto-geo), on a host that installs it."""
        source = Path(get_template("oya/base.html").origin.name).read_text(encoding="utf-8")
        self.assertNotIn("totoTileLayer", source)
        self.assertNotIn("tile.openstreetmap.org", source)
