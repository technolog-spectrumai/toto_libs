"""What the locations pages hand their scripts: data, never code (2026-10-02,
crown 41).

**Member text.** The map, a route's page, a zone's page and the route search
put their JSON into a JavaScript TEMPLATE literal — ``JSON.parse(`{{ x|escapejs
}}`)``. ``escapejs`` is for a quoted string: it leaves ``${…}`` alone, which a
template literal runs. So a route named ``${alert(document.domain)}``, an
address note, a vault file's title or a location's metadata ran in the
browser of every member who opened the page. The route search wrote the
coordinates from its own address bar into ``Number("…")`` with HTML escaping
only, so a link ending ``start_lat=\\&start_lng=+alert(1))],//`` ran too.
Each now arrives as a ``json_script`` block, read with ``JSON.parse`` of its
text.

**Theme colours.** The map and the route search coloured each kind of place
and each route mode from ``window.tailwind.config`` — the Play CDN's
configuration, which a host with the BUILT stylesheet (37c.15) never writes.
There every kind and every mode drew in one fallback colour. The theme's
colours now come with the page, built or not.

    manage.py test toto.locations.tests_page_scripts
"""

import json
import re
from unittest import mock

from django.urls import reverse

from toto.core.models import ColorMix, Platform, Theme
from toto.locations.models import Address
from toto.locations.tests_map_pages import PageTestCase

#: What a member types where a page puts it into a script.
MARK = "${alert(document.domain)}"
#: What a link puts in the route search's address bar.
REFLECTED = "+alert(document.domain))],//"
BUILT = "toto.core.templatetags.oya_tailwind.built_css_present"

SCRIPT = re.compile(r"<script(?P<attrs>\s[^>]*)?>(?P<body>.*?)</script>", re.S | re.I)


def scripts(html):
    """``(code, data)``: the inline scripts the browser runs, and the
    ``json_script`` blocks (``type="application/json"``), which are data."""
    code, data = [], []
    for match in SCRIPT.finditer(html):
        attrs = match.group("attrs") or ""
        (data if "application/json" in attrs else code).append(match.group("body"))
    return code, data


def data_block(html, element_id):
    found = re.search(rf'<script id="{element_id}" type="application/json">(.*?)</script>',
                      html, re.S)
    return json.loads(found.group(1)) if found else None


class ScriptDataCase(PageTestCase):
    def assert_data_not_code(self, response, text):
        """``text`` reaches the page's scripts as data only — and does reach
        it, so the test is not passing on an empty page."""
        code, data = scripts(response.content.decode())
        self.assertEqual([body for body in code if text in body], [],
                         f"{text!r} is in a script the browser runs")
        self.assertTrue(any(text in body for body in data), f"{text!r} reached no data block")


class MemberTextTests(ScriptDataCase):
    def test_the_map_hands_names_notes_and_file_titles_as_data(self):
        from toto.vault.models import VaultFile

        Address.objects.create(street="Long Street", locality_name="Town", note=MARK,
                               latitude=52.24, longitude=21.01)
        VaultFile.objects.create(owner=self.user, title=f"{MARK}.geojson", key="mark",
                                 file_type="json")
        page = self.open(reverse("locations:locations_all"))
        self.assert_data_not_code(page, MARK)
        self.assertEqual({row["note"] for row in data_block(page.content.decode(),
                                                            "locations-payload")
                          if row["type"] == "Address"}, {MARK})
        self.assertEqual([f["title"] for f in data_block(page.content.decode(),
                                                         "locations-vault-files")],
                         [f"{MARK}.geojson"])
        self.assertEqual(data_block(page.content.decode(), "locations-map-layers"), [])

    def test_a_routes_page_hands_its_name_and_notes_as_data(self):
        from django.contrib.gis.geos import LineString, MultiLineString

        from toto.locations.models import Route

        route = Route.objects.create(name=MARK, notes=MARK, created_by=self.user,
                                     geometry=MultiLineString(LineString((18.0, 54.0),
                                                                         (18.5, 54.5))))
        page = self.open(reverse("locations:route_detail", args=[route.pk]))
        self.assert_data_not_code(page, MARK)
        self.assertEqual(data_block(page.content.decode(), "route-payload")["name"], MARK)

    def test_a_zones_page_hands_its_name_as_data(self):
        from toto.locations.models import Zone

        zone = Zone.objects.create(name=MARK,
                                   geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        page = self.open(reverse("locations:zone_detail", args=[zone.pk]))
        self.assert_data_not_code(page, MARK)
        self.assertEqual(data_block(page.content.decode(), "zone-payload")["name"], MARK)

    def test_a_locations_metadata_is_handed_to_the_editor_as_data(self):
        address = Address.objects.create(street="Long Street", locality_name="Town",
                                         latitude=52.24, longitude=21.01,
                                         metadata={"remark": MARK}, created_by=self.user)
        page = self.open(reverse("locations:location_detail", args=["address", address.pk]))
        self.assert_data_not_code(page, MARK)
        self.assertEqual(json.loads(data_block(page.content.decode(), "location-metadata")),
                         {"remark": MARK})

    def test_the_route_search_hands_its_address_bar_as_data(self):
        page = self.open(reverse("locations:route_search"), {
            "mode": "car", "start_address": "", "end_address": "",
            "start_lat": "\\", "start_lng": REFLECTED, "end_lat": "54.4", "end_lng": "18.6"})
        self.assertNotEqual(page.context["error"], "")
        self.assert_data_not_code(page, REFLECTED)
        form = data_block(page.content.decode(), "route-search-form")
        self.assertEqual((form["start_lat"], form["start_lng"], form["mode"]),
                         ("\\", REFLECTED, "car"))

    def test_the_route_search_hands_the_routed_line_as_data(self):
        feature = {"type": "Feature", "properties": {"mode": "car", "distance_km": 1.0,
                                                     "duration_min": 2.0, "note": MARK},
                   "geometry": {"type": "LineString", "coordinates": [[18.6, 54.3],
                                                                      [18.7, 54.4]]}}
        with mock.patch("toto.locations.views.fetch_traversable_route", return_value=feature):
            page = self.open(reverse("locations:route_search"), {
                "mode": "car", "start_lat": "54.3", "start_lng": "18.6",
                "end_lat": "54.4", "end_lng": "18.7"})
        self.assert_data_not_code(page, MARK)
        self.assertEqual(data_block(page.content.decode(), "route-feature"), feature)


class ThemeColourTests(ScriptDataCase):
    """The map's kinds and the route modes in the theme's colours, with the
    built stylesheet as without it."""

    def setUp(self):
        super().setUp()
        mix = ColorMix.objects.create(name="Probe", link_light="#123456",
                                      success_light="#654321", accent_light="#abcdef")
        Platform.objects.filter(active=True).update(
            theme=Theme.objects.create(name="Probe", color_mix=mix))

    def colours(self, url, built):
        with mock.patch(BUILT, return_value=built):
            page = self.open(url)
        html = page.content.decode()
        self.assertEqual("oya/tailwind.css" in html, built)
        code, _data = scripts(html)
        self.assertEqual([body for body in code if "window.tailwind" in body], [],
                         "a script still reads the Play CDN's configuration")
        return data_block(html, "locations-theme-colors")

    def test_the_map_page_has_the_theme_colours_built_or_not(self):
        for built in (True, False):
            with self.subTest(built=built):
                colours = self.colours(reverse("locations:locations_all"), built)
                self.assertEqual((colours["link-light"], colours["success-light"],
                                  colours["accent-light"]), ("#123456", "#654321", "#abcdef"))

    def test_the_route_search_has_the_theme_colours_built_or_not(self):
        for built in (True, False):
            with self.subTest(built=built):
                colours = self.colours(reverse("locations:route_search"), built)
                self.assertEqual(colours["link-light"], "#123456")


def popup_arguments(code):
    """What each ``.bindPopup(…)`` in ``code`` is given, as written."""
    found, start = [], 0
    while (at := code.find(".bindPopup(", start)) != -1:
        depth, i = 1, at + len(".bindPopup(")
        while depth and i < len(code):
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        found.append(" ".join(code[at + len(".bindPopup("):i - 1].split()))
        start = i
    return found


class PopupTextTests(ScriptDataCase):
    """A Leaflet popup given a string renders it as HTML (2026-10-02, crown
    41): a route's end marker got its address's label, the People map a
    member's display name and the zone page the zone's name — a street or a
    name holding ``<img src=x onerror=…>`` ran when the popup opened. Each
    goes in as a text node now; what the pages pass is only that, or literal
    text of their own."""

    TEXT_ONLY = {"popupText(address.label)", "label",
                 '"Searching from here. Drag to move."', '"Drag me, then Search."'}

    def popups(self, url):
        code, _data = scripts(self.open(url).content.decode())
        return {argument for body in code for argument in popup_arguments(body)}

    def test_a_routes_end_markers_label_their_address_as_text(self):
        from django.contrib.gis.geos import LineString, MultiLineString

        from toto.locations.models import Route

        route = Route.objects.create(name="Walk", created_by=self.user,
                                     geometry=MultiLineString(LineString((18.0, 54.0),
                                                                         (18.5, 54.5))))
        popups = self.popups(reverse("locations:route_detail", args=[route.pk]))
        self.assertTrue(popups)
        self.assertLessEqual(popups, self.TEXT_ONLY)

    def test_a_zones_popup_names_it_as_text(self):
        from toto.locations.models import Zone

        zone = Zone.objects.create(name="Centre",
                                   geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        popups = self.popups(reverse("locations:zone_detail", args=[zone.pk]))
        self.assertTrue(popups)
        self.assertLessEqual(popups, self.TEXT_ONLY)

    def test_the_people_map_names_people_as_text(self):
        popups = self.popups(reverse("locations:people"))
        self.assertTrue(popups)
        self.assertLessEqual(popups, self.TEXT_ONLY)
