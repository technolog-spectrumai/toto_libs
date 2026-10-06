"""Map tiles come from a service that needs no key, and only a page that
draws a map loads Leaflet (2026-10-06; lifted from the parked map's
``tests_map_tiles``).

    manage.py test toto.geography.tests_map_tiles
"""

from pathlib import Path

from django.apps import apps
from django.template.loader import get_template
from django.test import SimpleTestCase

KEYED_TILE_HOSTS = ("basemaps.cartocdn.com",)


def templates_of(config):
    return (Path(config.path) / "templates").glob("**/*.html")


class MapTileTests(SimpleTestCase):
    def test_no_template_uses_a_tile_service_that_needs_a_key(self):
        offenders = []
        for config in apps.get_app_configs():
            for template in templates_of(config):
                text = template.read_text(encoding="utf-8", errors="replace")
                if any(host in text for host in KEYED_TILE_HOSTS):
                    offenders.append(str(template))
        self.assertEqual(offenders, [])

    def test_every_map_follows_dark_mode(self):
        offenders = []
        for config in apps.get_app_configs():
            for template in templates_of(config):
                text = template.read_text(encoding="utf-8", errors="replace")
                if "L.tileLayer(" in text and "toto-dark-tiles" not in text \
                        and "totoTileLayer" not in text:
                    offenders.append(str(template))
        self.assertEqual(offenders, [])

    def source(self, name):
        return Path(get_template(name).origin.name).read_text(encoding="utf-8")

    def test_the_tiles_partial_offers_the_shared_tile_layer(self):
        source = self.source("geography/_tiles.html")
        self.assertIn("window.totoTileLayer = function (map, options)", source)
        self.assertIn('box.classList.toggle("toto-dark-tiles"', source)
        self.assertIn("Alpine.effect", source)
        self.assertIn(".toto-dark-tiles", source)
        self.assertIn("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", source)

    def test_openstreetmap_s_tiles_are_the_only_outside_address(self):
        for config in (apps.get_app_config("geography"),):
            for template in templates_of(config):
                text = template.read_text(encoding="utf-8")
                hosts = {part.split("/")[2] for part in text.split() if "://" in part
                         for part in [part[part.index("://") - 5:]] if part.startswith("https")}
                self.assertLessEqual(hosts, {"{s}.tile.openstreetmap.org"}, str(template))

    def test_the_widget_is_the_one_place_that_loads_leaflet(self):
        widget = self.source("geography/_map.html")
        self.assertIn("{% static 'vendor/leaflet/leaflet.js' %}", widget)
        self.assertIn("{% static 'vendor/leaflet/leaflet.css' %}", widget)
        self.assertIn('{% include "geography/_tiles.html" %}', widget)
        self.assertIn("{% static 'geography/pin.js' %}", widget)
        self.assertNotIn("oya/pin.js", widget)
        self.assertNotIn("locations/", widget)
        for template in templates_of(apps.get_app_config("geography")):
            if template.name != "_map.html":
                self.assertNotIn("vendor/leaflet", template.read_text(encoding="utf-8"),
                                 str(template))

    def test_the_platform_s_base_template_draws_no_tiles(self):
        source = self.source("oya/base.html")
        self.assertNotIn("totoTileLayer", source)
        self.assertNotIn("openstreetmap", source)

    def test_the_widget_puts_its_data_in_no_script(self):
        widget = self.source("geography/_map.html")
        self.assertIn("{{ geo.config|json_script:geo.config_id }}", widget)
        self.assertIn('data-csrf="{{ csrf_token }}"', widget)
        self.assertNotIn("<script>", widget)
        for hint in ('{% price_hint "geography.lookup" %}', '{% price_hint "geography.route" %}',
                     '{% price_hint "geography.pin" %}', '{% price_hint "geography.zone" %}',
                     '{% price_hint "geography.note" %}'):
            self.assertIn(hint, widget)
