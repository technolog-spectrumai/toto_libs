"""Map tiles come from a service that needs no key (2026-09-26).

CARTO's dark basemap started answering every tile with "API key required",
so the dark layer on every Leaflet page is OpenStreetMap's own tiles with the
`toto-dark-tiles` filter (oya/base.html). This walks every installed app's
templates, so a keyed tile service cannot come back by copy-paste.
"""

from pathlib import Path

from django.template.loader import get_template
from django.test import SimpleTestCase

KEYED_TILE_HOSTS = ("basemaps.cartocdn.com",)


class MapTileTests(SimpleTestCase):
    def test_no_template_uses_a_tile_service_that_needs_a_key(self):
        """Every installed app's templates — the ones Django serves, not every
        copy of the namespace a test environment happens to carry."""
        from django.apps import apps

        offenders = []
        for config in apps.get_app_configs():
            for template in (Path(config.path) / "templates").glob("**/*.html"):
                text = template.read_text(encoding="utf-8", errors="replace")
                if any(host in text for host in KEYED_TILE_HOSTS):
                    offenders.append(str(template))
        self.assertEqual(offenders, [])

    def test_every_map_follows_dark_mode(self):
        """A page that draws tiles either uses the shared `totoTileLayer`
        (dark with the page, live) or carries its own `toto-dark-tiles` layer.
        The people map drew light tiles in dark mode (2026-09-28)."""
        from django.apps import apps

        offenders = []
        for config in apps.get_app_configs():
            for template in (Path(config.path) / "templates").glob("**/*.html"):
                text = template.read_text(encoding="utf-8", errors="replace")
                if "L.tileLayer(" in text and "toto-dark-tiles" not in text \
                        and "totoTileLayer" not in text:
                    offenders.append(str(template))
        self.assertEqual(offenders, [])

    def test_the_base_template_offers_the_shared_tile_layer(self):
        source = Path(get_template("oya/base.html").origin.name).read_text(encoding="utf-8")
        self.assertIn("window.totoTileLayer = function (map, options)", source)
        self.assertIn('box.classList.toggle("toto-dark-tiles"', source)
        self.assertIn("Alpine.effect", source)

    def test_the_base_template_carries_the_dark_tile_filter(self):
        source = Path(get_template("oya/base.html").origin.name).read_text(encoding="utf-8")
        self.assertIn(".toto-dark-tiles", source)
