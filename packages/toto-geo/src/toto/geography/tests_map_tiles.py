"""Map tiles come from a service that needs no key, and only a page that
draws a map loads Leaflet (2026-10-06; lifted from the parked map's
``tests_map_tiles``).

    manage.py test toto.geography.tests_map_tiles
"""

import json
import re
from pathlib import Path

from django.apps import apps
from django.template.loader import get_template, render_to_string
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

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

    def test_the_loader_is_the_one_place_that_loads_leaflet(self):
        """Since stage 64 two templates draw a map, the widget and the
        Locations page. Both include one loader, ``geography/_leaflet.html``,
        and it alone names Leaflet's files, the pin and the tile helper: a
        page that draws no map still carries none of them."""
        loader = self.source("geography/_leaflet.html")
        self.assertIn("{% static 'vendor/leaflet/leaflet.js' %}", loader)
        self.assertIn("{% static 'vendor/leaflet/leaflet.css' %}", loader)
        self.assertIn('{% include "geography/_tiles.html" %}', loader)
        self.assertIn("{% static 'geography/pin.js' %}", loader)
        self.assertNotIn("oya/pin.js", loader)
        self.assertNotIn("locations/", loader)
        including = []
        for template in templates_of(apps.get_app_config("geography")):
            text = template.read_text(encoding="utf-8")
            if template.name != "_leaflet.html":
                self.assertNotIn("vendor/leaflet", text, str(template))
                self.assertNotIn("geography/pin.js", text, str(template))
            if '{% include "geography/_leaflet.html" %}' in text:
                including.append(template.name)
        self.assertEqual(sorted(including), ["_map.html", "locations.html"])
        widget = self.source("geography/_map.html")
        self.assertNotIn("oya/pin.js", widget)
        self.assertNotIn("locations/", widget)

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


class RouteSwitchTests(TestCase):
    """The widget draws route search only for a page that asked for it
    (2026-10-06; the owner: "Route search exists exclusively in the
    Locations app. Remove route-search controls from SocialHub and other map
    views."). The profile's map and the community's do not ask
    (``tests_profile``, ``tests_headquarters``); this is the widget itself,
    asked and not asked. What the route panel then does, end by end, is run
    in ``tests_map_js`` on the elements this very template has."""

    ROUTE_MARKS = ('data-geo="route"', 'data-geo="from"', 'data-geo="to"', 'data-geo="mode"',
                   'data-geo="route-go"', 'data-geo="route-go-label"', 'data-geo="route-note"',
                   '<option value="car">', '<option value="bicycle">', '<option value="foot">',
                   "Find route")
    ROUTE_TEXTS = {"no_route", "route_summary", "choose_end", "end_gone", "choose_again"}

    def render(self, **options):
        from toto.geography.mapview import map_context
        from toto.geography.testing import member

        if not hasattr(self, "user"):
            self.user, _person = member("ada")
        request = RequestFactory().get("/")
        request.user = self.user
        geo = map_context("geography-widget", **options)
        html = render_to_string("geography/_map.html", {"geo": geo}, request=request)
        config = json.loads(re.search(
            r'<script id="geography-widget-config" type="application/json">(.*?)</script>',
            html, re.S).group(1))
        return geo, html, config

    def prices(self, html):
        return re.findall(r'data-mana-role="(\w+)"', html)

    def economy(self):
        from django.core.cache import cache

        from toto.geography.testing import Economy, fresh_cache

        fresh_cache(self)
        Economy(self)
        cache.clear()       # the rate card is read through the cache

    def test_a_page_that_asks_gets_the_panel_its_price_and_the_door(self):
        self.economy()
        geo, html, config = self.render(routes=True)
        for mark in self.ROUTE_MARKS:
            self.assertIn(mark, html)
        self.assertNotIn("public_transport", html)
        self.assertEqual([value for value, _label in geo["route_modes"]],
                         ["car", "bicycle", "foot"])
        self.assertEqual(config["urls"], {"search": reverse("geography:search"),
                                          "route": reverse("geography:route")})
        self.assertLessEqual(self.ROUTE_TEXTS, set(config["texts"]))
        self.assertEqual(self.prices(html), ["compute", "compute"],
                         "a price beside Search and one beside Find route")
        panel = html[html.index('data-geo="route"'):]
        self.assertEqual(self.prices(panel), ["compute"], "the route's price is in its panel")

    def test_a_page_that_does_not_ask_gets_none_of_it(self):
        self.economy()
        for options in ({}, {"routes": False}):
            with self.subTest(options=options):
                geo, html, config = self.render(**options)
                for mark in self.ROUTE_MARKS + (reverse("geography:route"), "fa-route"):
                    self.assertNotIn(mark, html)
                self.assertEqual(geo["route_modes"], [])
                self.assertEqual(config["urls"], {"search": reverse("geography:search")})
                self.assertEqual(self.ROUTE_TEXTS & set(config["texts"]), set())
                self.assertEqual(self.prices(html), ["compute"], "the search's price alone")
                self.assertIn('data-geo="search-go"', html)

    @override_settings(GEOGRAPHY_ROUTING={"enabled": False})
    def test_asking_is_not_enough_where_the_host_has_routing_off(self):
        geo, html, config = self.render(routes=True)
        for mark in self.ROUTE_MARKS + (reverse("geography:route"),):
            self.assertNotIn(mark, html)
        self.assertNotIn("route", config["urls"])
        self.assertEqual(self.ROUTE_TEXTS & set(config["texts"]), set())

    def test_the_two_pages_of_the_stage_do_not_ask(self):
        """As text: neither plugin passes ``routes`` to ``map_context``."""
        import inspect

        from toto.geography.plugins import community_plugins, profile_plugins

        for module in (profile_plugins, community_plugins):
            calls = re.findall(r"map_context\((.*?)\n        \)", inspect.getsource(module), re.S)
            self.assertEqual(len(calls), 1, module.__name__)
            self.assertNotIn("routes", calls[0], module.__name__)

    def test_the_panel_of_the_template_is_the_one_the_script_tests_run_on(self):
        from toto.geography import tests_map_js

        names = {part["name"] for part in tests_map_js.template_parts()}
        self.assertLessEqual(tests_map_js.ROUTE_PANEL, names)
        _geo, html, config = self.render(routes=True)
        for name in tests_map_js.ROUTE_PANEL:
            self.assertIn(f'data-geo="{name}"', html)
        self.assertLessEqual(self.ROUTE_TEXTS, set(tests_map_js.TEXTS))
        self.assertEqual(config["urls"]["route"], tests_map_js.URLS["route"])
