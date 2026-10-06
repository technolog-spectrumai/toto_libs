"""The Locations page (stage 64, 2026-10-06): one page for every signed-in
member, with everything they may see, the route panel (here and nowhere
else), a price beside every charged control, and no charge for looking.

Since the same day the tools beside the map are tabs (the index, Search
nearby, Route), the community filter is a dropdown of checkboxes, and a
pin's and a zone's words are typed in a dialog and nowhere else. What the
script does with them is run in ``tests_locations_js``.

    manage.py test toto.geography.tests_locations_page
"""

import json
import re

from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import reverse

from toto.geography import access, saves
from toto.geography.models import GeographyUsageEvent
from toto.geography.testing import client_of, element, holds, op, tree_of, walk, words_fields
from toto.geography.locations_testing import LocationsCase

CONFIG = re.compile(
    r'<script id="geography-locations-config" type="application/json">(.*?)</script>', re.S)
ROUTING = {"enabled": True, "timeout": 10,
           "endpoints": {"car": "https://router.example/routed-car/route/v1/driving"}}


def config_of(text):
    return json.loads(CONFIG.search(text).group(1))


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class PageTests(LocationsCase):
    def page(self, user, query=""):
        response = client_of(user).get(self.page_url + query)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_the_address_and_who_gets_it(self):
        self.assertEqual(self.page_url, reverse("geography:locations"))
        for user in (self.member_user, self.staff_user, self.root, self.other_user):
            with self.subTest(user=user.username):
                text = self.page(user)
                self.assertIn("data-geography-locations", text)
                self.assertIn("geography/locations.js", text)
                self.assertIn("vendor/leaflet/leaflet.js", text)

    def test_the_route_panel_is_here_with_its_door(self):
        text = self.page(self.member_user)
        self.assertIn('data-testid="geography-route-panel"', text)
        self.assertIn('data-testid="geography-route-go"', text)
        config = config_of(text)
        self.assertEqual(config["urls"]["route"], reverse("geography:route"))
        self.assertEqual(config["urls"]["search"], reverse("geography:search"))
        self.assertIn("route_summary", config["texts"])

    @override_settings(GEOGRAPHY_ROUTING={"enabled": False})
    def test_no_panel_where_the_host_has_routing_off(self):
        text = self.page(self.member_user)
        self.assertNotIn("geography-route-panel", text)
        self.assertNotIn("route", config_of(text)["urls"])

    def test_the_rows_their_kinds_and_the_counts(self):
        self.pin()
        self.zone()
        saves.save_headquarters(self.head_user, self.guild, lat=54.3, lng=18.6, name="House",
                                note="ring", op=op())
        saves.save_zone(self.head_user, self.guild, name="Area", description="",
                        outline=[[54.0, 18.0], [54.0, 18.1], [54.1, 18.1]], op=op())
        saves.save_person_point(self.member_user, self.member, lat=50.0, lng=19.9,
                                name="Home", note="", op=op())
        text = self.page(self.member_user)
        config = config_of(text)
        self.assertEqual(sorted(row["kind"] for row in config["rows"]),
                         ["area", "headquarters", "person", "pin", "zone"])
        for kind in ("person", "headquarters", "area", "pin", "zone"):
            self.assertRegex(text, rf'data-count="{kind}">1<')
        pin = next(row for row in config["rows"] if row["kind"] == "pin")
        self.assertEqual((pin["community"], pin["author"], pin["may_edit"], pin["may_moderate"]),
                         ({"slug": self.guild.slug, "name": "Guild"}, "mia", True, False))
        self.assertTrue(pin["urls"]["detail"].endswith(f"/pins/{pin['uid']}/"))
        self.assertFalse([row for row in config["rows"] if "pk" in row])
        head = config_of(self.page(self.head_user))
        self.assertTrue(next(r for r in head["rows"] if r["kind"] == "pin")["may_moderate"])
        self.assertEqual([c["slug"] for c in config["communities"]], [self.guild.slug])
        self.assertEqual(config_of(self.page(self.staff_user))["communities"], [])
        # An administrator: every community (the host may have more of its own).
        self.assertLessEqual({self.guild.slug, self.other.slug},
                             {c["slug"] for c in config_of(self.page(self.root))["communities"]})

    def test_markup_in_a_name_reaches_the_page_as_data_only(self):
        self.pin(name="<img src=x onerror=alert(1)>", note="</script><b>x</b>")
        text = self.page(self.member_user)
        self.assertNotIn("<img src=x", text)
        self.assertNotIn("</script><b>", text)
        row = next(r for r in config_of(text)["rows"] if r["kind"] == "pin")
        self.assertEqual(row["name"], "<img src=x onerror=alert(1)>")
        details = client_of(self.member_user).get(row["urls"]["detail"]).content.decode()
        self.assertNotIn("<img src=x", details)
        self.assertIn("&lt;img src=x", details)

    def test_the_script_writes_text_never_markup(self):
        source = open(finders.find("geography/locations.js"), encoding="utf-8").read()
        self.assertEqual(source.count(".innerHTML"), 1)     # the server's escaped details
        self.assertNotIn("insertAdjacentHTML", source)
        self.assertNotIn("document.write", source)
        self.assertNotRegex(source, r"bindTooltip\(\s*(row|hit|point)\.")
        self.assertNotIn("localStorage", source)
        self.assertNotIn("sessionStorage", source)

    def test_the_community_filter_and_the_opened_row_come_from_the_address(self):
        """The community page's link carries one community; the page's own
        address carries each ticked one. The page is handed them as a list,
        each once, in the address's order; none is an empty list, which is
        every community."""
        guild, other = self.guild.slug, self.other.slug
        config = config_of(self.page(self.member_user, f"?community={guild}&open=pin:abc"))
        self.assertEqual((config["filter"], config["open"]), ([guild], "pin:abc"))
        several = config_of(self.page(
            self.member_user, f"?community={other}&community={guild}&community={other}&community="))
        self.assertEqual(several["filter"], [other, guild])
        self.assertEqual(config_of(self.page(self.member_user))["filter"], [])
        # A slug that names nothing is data like any other: the page ticks
        # only the communities it knows (tests_locations_js).
        self.assertEqual(config_of(self.page(self.member_user, "?community=no-such"))["filter"],
                         ["no-such"])
        # The same rows whatever is ticked: the filter is the page's own.
        self.pin()
        self.assertEqual(config_of(self.page(self.member_user, f"?community={other}"))["rows"],
                         config_of(self.page(self.member_user))["rows"])

    def test_the_community_filter_is_a_dropdown_of_checkboxes(self):
        """The owner, 2026-10-06: "istead of community choice in locations
        app there should be dropdown with checkboxes filter by community"."""
        text = self.page(self.member_user)
        tree = tree_of(text)
        self.assertIsNone(element(tree, "data-geo", "community"), "the single choice is gone")
        box = element(tree, "data-geo", "community-filter")
        button = element(box["children"], "data-geo", "community-toggle")
        self.assertEqual((button["tag"], button["attrs"]["type"], button["attrs"]["aria-expanded"],
                          button["attrs"]["aria-controls"]),
                         ("button", "button", "false", "geography-community-list"))
        listing = element(box["children"], "id", "geography-community-list")
        self.assertEqual((listing["attrs"]["role"], listing["attrs"]["data-geo"]),
                         ("group", "community-list"))
        self.assertIn("hidden", listing["attrs"]["class"].split())
        labelled = listing["attrs"]["aria-labelledby"]
        self.assertIsNotNone(element(box["children"], "id", labelled))
        for name in ("community-all", "community-none", "community-boxes"):
            self.assertTrue(holds(listing, "data-geo", name), name)
        # The boxes are made by the script from the page's data, each name
        # as text: no community's name is markup in the page.
        self.assertEqual(element(listing["children"], "data-geo", "community-boxes")["children"],
                         [])
        self.assertIn("All communities", text)
        texts = config_of(text)["texts"]
        self.assertEqual((texts["all_communities"], texts["n_communities"]),
                         ("All communities", "%(n)s communities"))
        # It is in the index's tab, with the other filters.
        self.assertTrue(holds(element(tree, "data-geo-panel", "index"), "data-geo",
                              "community-filter"))

    def test_looking_charges_nothing(self):
        row = self.pin()
        area = self.zone()
        before = GeographyUsageEvent.objects.count()
        client = client_of(self.member_user)
        for _ in range(3):
            client.get(self.page_url)
            client.get(self.url("pin_detail", row))
            client.get(self.url("zone_detail", area))
            client.get(reverse("geography:my_contributions"))
        self.assertEqual(GeographyUsageEvent.objects.count(), before)

    def test_the_cap_is_stated_when_it_is_reached(self):
        self.pin()
        self.pin(name="Second")
        self.assertNotIn("geography-capped", self.page(self.member_user))
        original = access.ROW_CAP
        access.ROW_CAP = 1
        self.addCleanup(setattr, access, "ROW_CAP", original)
        text = self.page(self.member_user)
        self.assertIn('data-testid="geography-capped"', text)
        self.assertEqual(len([r for r in config_of(text)["rows"] if r["kind"] == "pin"]), 1)

    def test_the_details_of_a_row(self):
        row = self.pin()
        own = client_of(self.member_user).get(self.url("pin_detail", row))
        self.assertEqual(own["Cache-Control"], "no-store")
        text = own.content.decode()
        for part in ("Well", "Rynek 1", "open on Sundays", "Guild", "by mia",
                     'data-geo-change="pin"', 'data-geo-act="delete"', "comment-thread"):
            self.assertIn(part, text)
        self.assertNotIn('data-geo-act="hide"', text)
        other = client_of(self.senior_user).get(self.url("pin_detail", row)).content.decode()
        self.assertNotIn("data-geo-change", other)
        self.assertNotIn("data-geo-act", other)

    def test_the_details_hold_no_field_for_the_words_only_the_button_that_opens_the_dialog(self):
        """The author's change is typed in the page's dialog. The details
        hand it the words as data on one button (escaped, line breaks kept)
        and the door; they hold no input for a name, an address, a note or
        a description."""
        pin = self.pin(name='A "well" <b>', note="line one\nline two")
        area = self.zone(description="by <the> river")
        client = client_of(self.member_user)
        for kind, row, words in (
                ("pin", pin, {"data-name": 'A "well" <b>', "data-postal": "Rynek 1",
                              "data-note": "line one\nline two"}),
                ("zone", area, {"data-name": "Meadow", "data-description": "by <the> river"})):
            with self.subTest(kind=kind):
                text = client.get(self.url(f"{kind}_detail", row)).content.decode()
                self.assertNotIn("data-geo-edit", text)
                for name in ("name", "postal_address", "note", "description"):
                    self.assertNotIn(f'name="{name}"', text)
                self.assertNotIn("<b>", text)
                tree = tree_of(text)
                button = element(tree, "data-geo-change", kind)
                self.assertEqual(button["tag"], "button")
                self.assertEqual(button["attrs"]["data-url"], self.url(f"{kind}_detail", row))
                for key, value in words.items():
                    self.assertEqual(button["attrs"][key], value)
                self.assertEqual({key for key in button["attrs"] if key.startswith("data-")},
                                 {"data-geo-change", "data-testid", "data-url", *words})
                # No field of the thread is one of a pin's words either.
                fields = [node["attrs"].get("name") for node, _above in walk(tree)
                          if node["tag"] in ("input", "textarea", "select")]
                self.assertLessEqual({name for name in fields if name}, {"body", "op",
                                                                         "csrfmiddlewaretoken",
                                                                         "parent"}, fields)


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class TabTests(LocationsCase):
    """The tools beside the map are tabs (the owner, 2026-10-06: "Search
    nearby and Route in locations should be different tabs"): the index,
    Search nearby, Route. The server draws the one the address asks for."""

    NAMES = ["index", "nearby", "route"]

    def tree(self, user=None, query=""):
        response = client_of(user or self.member_user).get(self.page_url + query)
        self.assertEqual(response.status_code, 200)
        return tree_of(response.content.decode())

    def strip(self, tree):
        """``(tablist, tabs, panels)`` of the page."""
        tablist = element(tree, "role", "tablist")
        tabs = [node for node, _above in walk(tablist["children"])
                if node["attrs"].get("role") == "tab"]
        panels = [node for node, _above in walk(tree) if node["attrs"].get("role") == "tabpanel"]
        return tablist, tabs, panels

    def open_tab(self, tree):
        _tablist, tabs, panels = self.strip(tree)
        selected = [tab["attrs"]["data-geo-tab"] for tab in tabs
                    if tab["attrs"]["aria-selected"] == "true"]
        shown = [panel["attrs"]["data-geo-panel"] for panel in panels
                 if "hidden" not in panel["attrs"]["class"].split()]
        in_order = [tab["attrs"]["data-geo-tab"] for tab in tabs
                    if tab["attrs"]["tabindex"] == "0"]
        self.assertEqual(selected, shown)
        self.assertEqual(selected, in_order)
        self.assertEqual(len(selected), 1, "one tab is open at a time")
        return selected[0]

    def test_a_tablist_three_tabs_and_their_panels(self):
        tree = self.tree()
        tablist, tabs, panels = self.strip(tree)
        self.assertTrue(tablist["attrs"]["aria-label"])
        self.assertEqual([tab["attrs"]["data-geo-tab"] for tab in tabs], self.NAMES)
        self.assertEqual([panel["attrs"]["data-geo-panel"] for panel in panels], self.NAMES)
        for tab, panel in zip(tabs, panels):
            with self.subTest(tab=tab["attrs"]["data-geo-tab"]):
                self.assertEqual((tab["tag"], tab["attrs"]["type"]), ("button", "button"))
                self.assertEqual(tab["attrs"]["aria-controls"], panel["attrs"]["id"])
                self.assertEqual(panel["attrs"]["aria-labelledby"], tab["attrs"]["id"])
                self.assertIn(tab["attrs"]["aria-selected"], ("true", "false"))
        self.assertEqual(self.open_tab(tree), "index")
        # No other tablist and no panel outside the three.
        self.assertEqual(len([1 for node, _a in walk(tree)
                              if node["attrs"].get("role") == "tablist"]), 1)

    def test_each_tool_is_in_its_own_tab_and_the_map_in_none(self):
        tree = self.tree()
        index, nearby, route = self.strip(tree)[2]
        inside = {
            "index": ("filter", "community-filter", "fit", "reset", "index", "shown"),
            "nearby": ("nearby", "nearby-centre", "nearby-radius", "nearby-go", "nearby-clear",
                       "nearby-note", "nearby-results"),
            "route": ("route", "from", "to", "mode", "route-go", "route-note"),
        }
        for panel in (index, nearby, route):
            name = panel["attrs"]["data-geo-panel"]
            for other, marks in inside.items():
                for mark in marks:
                    with self.subTest(panel=name, mark=mark):
                        self.assertEqual(holds(panel, "data-geo", mark), other == name)
        self.assertTrue(holds(index, "data-geo-kind", "pin"))
        self.assertTrue(holds(route, "data-testid", "geography-route-panel"))
        self.assertTrue(holds(route, "data-testid", "geography-route-go"))
        # The route's price is beside its button, in its tab; nearby has none.
        self.assertFalse(holds(nearby, "data-mana-role"))
        # Shared by the three, so in none of them: the map, the place search
        # (a centre and a route's end are both taken from its hits), the
        # click menu, the tool that places a pin, the details, the dialogs.
        for mark in ("map", "q", "search-go", "results", "click-menu", "pin-tool",
                     "details", "pin-dialog", "zone-dialog"):
            node = element(tree, "data-geo", mark)
            self.assertIsNotNone(node, mark)
            for panel in (index, nearby, route):
                self.assertFalse(holds(panel, "data-geo", mark), mark)
        # The three are in the drawer, which a phone opens at each of them.
        drawer = element(tree, "data-geo", "drawer")
        self.assertTrue(holds(drawer, "role", "tablist"))
        self.assertEqual(len([1 for node, _a in walk(drawer["children"])
                              if node["attrs"].get("role") == "tabpanel"]), 3)
        openers = element(tree, "data-geo", "drawer-openers")
        self.assertEqual([node["attrs"]["data-geo-open"] for node, _a in walk(openers["children"])
                          if "data-geo-open" in node["attrs"]], self.NAMES)
        self.assertIn("lg:hidden", openers["attrs"]["class"].split())
        self.assertFalse(holds(drawer, "data-geo-open"))

    def test_the_address_names_the_open_tab(self):
        for query, name in (("", "index"), ("?tool=index", "index"), ("?tool=nearby", "nearby"),
                            ("?tool=route", "route"), ("?tool=no-such", "index"),
                            ("?tool=", "index"), ("?tool=route&tool=nearby", "nearby"),
                            (f"?community={self.guild.slug}&tool=route&open=pin:abc", "route")):
            with self.subTest(query=query):
                self.assertEqual(self.open_tab(self.tree(query=query)), name)

    @override_settings(GEOGRAPHY_ROUTING={"enabled": False})
    def test_without_route_search_there_are_two_tabs_and_no_route_tab_to_ask_for(self):
        for query in ("", "?tool=route"):
            with self.subTest(query=query):
                tree = self.tree(query=query)
                _tablist, tabs, panels = self.strip(tree)
                self.assertEqual([tab["attrs"]["data-geo-tab"] for tab in tabs],
                                 ["index", "nearby"])
                self.assertEqual([panel["attrs"]["data-geo-panel"] for panel in panels],
                                 ["index", "nearby"])
                self.assertEqual(self.open_tab(tree), "index")
                self.assertIsNone(element(tree, "data-geo-open", "route"))

    def test_asking_for_a_tab_changes_nothing_else_and_charges_nothing(self):
        self.pin()
        before = GeographyUsageEvent.objects.count()
        client = client_of(self.member_user)
        plain = config_of(client.get(self.page_url).content.decode())
        for name in self.NAMES:
            asked = config_of(client.get(f"{self.page_url}?tool={name}").content.decode())
            self.assertEqual(asked, plain, name)
        self.assertEqual(GeographyUsageEvent.objects.count(), before)


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class DialogMarkupTests(LocationsCase):
    """A pin's and a zone's words are typed in a dialog and nowhere else
    (the owner, 2026-10-06: "the information like name etc should be
    inputed via modal and modal only")."""

    def test_no_field_for_the_words_is_outside_a_dialog(self):
        for user in (self.member_user, self.head_user, self.staff_user, self.root):
            with self.subTest(user=user.username):
                text = client_of(user).get(self.page_url).content.decode()
                outside, inside = words_fields(text, "data-geography-locations")
                self.assertEqual(outside, [])
                self.assertEqual(inside, ["pin-community", "pin-name", "pin-postal", "pin-note",
                                          "zone-name", "zone-description"])

    def test_the_two_dialogs_are_the_library_s_modal(self):
        tree = tree_of(client_of(self.member_user).get(self.page_url).content.decode())
        box = element(tree, "data-geography-locations")
        dialogs = [node for node, _above in walk(box["children"])
                   if node["attrs"].get("role") == "dialog"]
        self.assertEqual([node["attrs"]["data-geo"] for node in dialogs],
                         ["pin-dialog", "zone-dialog"])
        # The pin's: a new one and a change (the title, the community and the
        # price of each, one of the two drawn at a time). The zone's: its
        # author's change only, for no zone is drawn on this page.
        both = ["change"] * 3 + ["new"] * 3
        for node, parts, expected in zip(dialogs, (
                ("pin-community", "pin-name", "pin-postal", "pin-note",
                 "pin-status", "pin-save", "pin-dialog-cancel"),
                ("zone-community-fixed", "zone-name", "zone-description",
                 "zone-status", "zone-save", "zone-dialog-cancel")), (both, [])):
            attrs = node["attrs"]
            with self.subTest(dialog=attrs["data-geo"]):
                self.assertEqual((attrs["aria-modal"], attrs["x-show"], attrs["x-trap"]),
                                 ("true", "open", "open"))
                self.assertIn("x-cloak", attrs)
                self.assertIn("@keydown.escape.window", attrs)
                self.assertIsNotNone(element(node["children"], "id", attrs["aria-labelledby"]))
                for part in parts:
                    self.assertTrue(holds(node, "data-geo", part), part)
                modes = [n["attrs"]["data-geo-mode"] for n, _a in walk(node["children"])
                         if "data-geo-mode" in n["attrs"]]
                self.assertEqual(sorted(modes), expected)

    def test_beside_the_map_a_tool_holds_the_geometry_only(self):
        tree = tree_of(client_of(self.member_user).get(self.page_url).content.decode())
        for tool, parts in (("pin-tool", ("pin-continue", "pin-cancel", "pin-tool-status")),):
            node = element(tree, "data-geo", tool)
            with self.subTest(tool=tool):
                self.assertIn("hidden", node["attrs"]["class"].split())
                for part in parts:
                    self.assertTrue(holds(node, "data-geo", part), part)
                self.assertEqual([n["tag"] for n, _a in walk(node["children"])
                                  if n["tag"] in ("input", "textarea", "select")], [])
        for gone in ("pin-form", "zone-form"):
            self.assertIsNone(element(tree, "data-geo", gone))

    def test_no_zone_is_drawn_on_this_page(self):
        """The owner, 2026-10-06: "remove 'draw community zone' from general
        locations tab". Nobody is offered it, the head and an administrator
        included; the zones that exist are still rows of the page."""
        self.zone()
        for user in (self.member_user, self.head_user, self.staff_user, self.root):
            with self.subTest(user=user.username):
                text = client_of(user).get(self.page_url).content.decode()
                tree = tree_of(text)
                for gone in ("zone-open", "zone-tool", "zone-undo", "zone-restart",
                             "zone-count", "zone-continue", "zone-cancel", "zone-community"):
                    self.assertIsNone(element(tree, "data-geo", gone), gone)
                self.assertIsNone(element(tree, "data-testid", "geography-draw-zone"))
                self.assertNotIn("Draw a community zone", text)
                self.assertNotIn("zone_draw.js", text)
                # The door that makes one is named nowhere (a saved zone's
                # own address is longer: it ends with the zone's uid).
                door = reverse("geography:zone_create", kwargs={"slug": self.guild.slug})
                self.assertNotIn(f'"{door}"', text)
                config = config_of(text)
                for community in config["communities"]:
                    self.assertNotIn("zones", community)
                if user in (self.member_user, self.head_user):
                    self.assertIn("zone", [row["kind"] for row in config["rows"]])


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class PriceTests(LocationsCase):
    billed = True

    def test_every_charged_control_carries_its_price(self):
        text = client_of(self.member_user).get(self.page_url).content.decode()
        # Search, Find route, in the pin's dialog the price of a new one and
        # the price of a change, in the zone's the price of a change.
        self.assertGreaterEqual(text.count("data-mana-role="), 5)
        self.assertIn('data-mana-role="compute"', text)
        self.assertIn('data-mana-role="storage"', text)
        tree = tree_of(text)
        node = element(tree, "data-geo", "pin-dialog")
        priced = [up["attrs"]["data-geo-mode"] for n, above in walk([node])
                  if n["attrs"].get("data-mana-role") == "storage"
                  for up in above if "data-geo-mode" in up["attrs"]]
        self.assertEqual(sorted(set(priced)), ["change", "new"])
        node = element(tree, "data-geo", "zone-dialog")
        self.assertEqual(len([n for n, _a in walk([node])
                              if n["attrs"].get("data-mana-role") == "storage"]), 1,
                         "a zone's change, and no price of a new zone")
        row = self.pin()
        client_of(self.member_user).post(self.url("pin_comment_add", row),
                                         {"body": "Hello", "op": op()})
        details = client_of(self.member_user).get(self.url("pin_detail", row)).content.decode()
        # Reply, Comment. The price of a change is beside Save, in the dialog.
        self.assertGreaterEqual(details.count('data-mana-role="storage"'), 2)
