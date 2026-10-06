"""The Locations page (stage 64, 2026-10-06): one page for every signed-in
member, with everything they may see, the route panel (here and nowhere
else), a price beside every charged control, and no charge for looking.

    manage.py test toto.geography.tests_locations_page
"""

import json
import re

from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import reverse

from toto.geography import access, saves
from toto.geography.models import GeographyUsageEvent
from toto.geography.testing import client_of, op
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
        config = config_of(self.page(self.member_user,
                                     f"?community={self.guild.slug}&open=pin:abc"))
        self.assertEqual((config["filter"], config["open"]), (self.guild.slug, "pin:abc"))

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
                     'data-geo-edit="pin"', 'data-geo-act="delete"', "comment-thread"):
            self.assertIn(part, text)
        self.assertNotIn('data-geo-act="hide"', text)
        other = client_of(self.senior_user).get(self.url("pin_detail", row)).content.decode()
        self.assertNotIn("data-geo-edit", other)
        self.assertNotIn("data-geo-act", other)


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class PriceTests(LocationsCase):
    billed = True

    def test_every_charged_control_carries_its_price(self):
        text = client_of(self.member_user).get(self.page_url).content.decode()
        # Search, Save pin, Save zone, Find route.
        self.assertGreaterEqual(text.count("data-mana-role="), 4)
        self.assertIn('data-mana-role="compute"', text)
        self.assertIn('data-mana-role="storage"', text)
        row = self.pin()
        client_of(self.member_user).post(self.url("pin_comment_add", row),
                                         {"body": "Hello", "op": op()})
        details = client_of(self.member_user).get(self.url("pin_detail", row)).content.decode()
        # Save changes, Reply, Comment.
        self.assertGreaterEqual(details.count('data-mana-role="storage"'), 3)
