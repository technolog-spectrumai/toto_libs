"""What SocialHub shows after stage 64 (2026-10-06): the community's page
keeps its map section and gains that community's pins and zones on it, read
only, for those who may see them, with a link into the Locations app; the
profile keeps its map with the person's point. Neither has a route panel:
route search is the Locations app's alone.

    manage.py test toto.geography.tests_community_map
"""

import json
import re

from django.contrib.staticfiles import finders
from django.test import override_settings
from django.urls import reverse

from toto.geography import saves
from toto.geography.models import CommunityPin
from toto.geography.testing import client_of, op
from toto.geography.locations_testing import AREA, LocationsCase

CONFIG = re.compile(
    r'<script id="geography-headquarters-config" type="application/json">(.*?)</script>', re.S)
ROUTING = {"enabled": True, "timeout": 10,
           "endpoints": {"car": "https://router.example/routed-car/route/v1/driving"}}


@override_settings(LOCATIONS_GEOCODING={"enabled": True}, GEOGRAPHY_ROUTING=ROUTING)
class CommunityMapTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.row = self.pin()
        self.area = self.zone()
        self.community_url = reverse("socialhub:community_detail",
                                     kwargs={"slug": self.guild.slug})

    def page(self, user, url=None):
        response = client_of(user).get(url or self.community_url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def config(self, text):
        return json.loads(CONFIG.search(text).group(1))

    def test_a_member_sees_the_community_s_pins_and_zones_read_only(self):
        for user in (self.member_user, self.senior_user, self.head_user, self.root):
            with self.subTest(user=user.username):
                text = self.page(user)
                config = self.config(text)
                self.assertEqual([(p["kind"], p["label"]) for p in config["points"]],
                                 [("pin", "Well")])
                self.assertEqual(config["zones"], [{"label": "Meadow",
                                                    "outline": AREA["outline"]}])
                self.assertIn(f'{reverse("geography:locations")}?community={self.guild.slug}',
                              text)
                # Read only: no door of a contribution is named on this page.
                self.assertNotIn("/pins/", text)
                self.assertNotIn(str(self.row.uid), text)
                self.assertNotIn("open on Sundays", text)       # a pin's note is not drawn

    def test_who_does_not_belong_sees_none_of_them(self):
        saves.save_headquarters(self.head_user, self.guild, lat=54.3, lng=18.6, name="House",
                                note="", op=op())
        for user in (self.other_user, self.staff_user):
            with self.subTest(user=user.username):
                text = self.page(user)
                config = self.config(text)
                self.assertEqual([p["kind"] for p in config["points"]], ["headquarters"])
                self.assertNotIn("zones", config)
                for hidden in ("Well", "Meadow"):
                    self.assertNotIn(hidden, text)

    def test_without_headquarters_a_stranger_gets_no_map_at_all(self):
        text = self.page(self.other_user)
        self.assertNotIn("data-geography-map", text)
        self.assertNotIn("Well", text)
        self.assertIn("data-geography-map", self.page(self.member_user))

    def test_a_hidden_row_is_not_drawn_here(self):
        CommunityPin.objects.filter(pk=self.row.pk).update(hidden_at=self.row.created_at)
        for user in (self.member_user, self.head_user):
            self.assertEqual(self.config(self.page(user))["points"], [])

    def test_no_route_panel_on_the_community_s_map_or_the_profile_s(self):
        saves.save_person_point(self.member_user, self.member, lat=50.0, lng=19.9,
                                name="Home", note="", op=op())
        profile = reverse("socialhub:profile_details", kwargs={"slug": self.member.slug})
        for text in (self.page(self.member_user), self.page(self.member_user, profile)):
            self.assertIn("data-geography-map", text)
            self.assertNotIn("geography-route-go", text)
            self.assertNotIn(reverse("geography:route"), text)
        # And it is on the Locations page.
        self.assertIn("geography-route-go", self.page(self.member_user, self.page_url))

    def test_the_profile_links_its_owner_to_their_rows(self):
        profile = reverse("socialhub:profile_details", kwargs={"slug": self.member.slug})
        own = self.page(self.member_user, profile)
        self.assertIn(reverse("geography:my_contributions"), own)
        self.member.show_address = True
        self.member.save(update_fields=["show_address"])
        saves.save_person_point(self.member_user, self.member, lat=50.0, lng=19.9,
                                name="Home", note="", op=op())
        self.assertNotIn(reverse("geography:my_contributions"),
                         self.page(self.head_user, profile))

    def test_the_widget_draws_the_extra_zones_as_text(self):
        source = open(finders.find("geography/map.js"), encoding="utf-8").read()
        self.assertIn("(config.zones || []).forEach", source)
        self.assertIn("shape.bindTooltip(asText(zone.label))", source)
