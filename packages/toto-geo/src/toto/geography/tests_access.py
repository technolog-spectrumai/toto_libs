"""The access rule of the Locations app (stage 64, 2026-10-06): every route
carries a mark, ``visible_to`` decides what a member is handed, and each door
answers 404 for what the member may not see and 403 for what they may not do.

    manage.py test toto.geography.tests_access
"""

import json
import re

from django.urls import reverse

from toto.geography import access, urls
from toto.geography.models import CommunityPin, CommunityZone
from toto.geography.testing import client_of, member, op, post
from toto.geography.locations_testing import AREA, PIN, LocationsCase

MARKS = {"signed-in", "moderator", "community", "contribution", "author"}
CONFIG = re.compile(
    r'<script id="geography-locations-config" type="application/json">(.*?)</script>', re.S)


def rows_of(response):
    return json.loads(CONFIG.search(response.content.decode()).group(1))["rows"]


class MarkTests(LocationsCase):
    def test_every_route_carries_a_mark(self):
        for pattern in urls.urlpatterns:
            self.assertIn(getattr(pattern.callback, "geography_door", None), MARKS,
                          pattern.name)

    def test_the_marks_by_name(self):
        expected = {"locations": "signed-in", "search": "signed-in", "route": "signed-in",
                    "my_address": "signed-in", "headquarters": "moderator",
                    "zone": "moderator", "pin_create": "community",
                    "zone_create": "community", "pin_detail": "contribution",
                    "zone_hide": "contribution", "pin_comment_add": "contribution",
                    "zone_comment_withdraw": "contribution",
                    "my_contributions": "author", "my_pin_delete": "author",
                    "my_comment_withdraw": "author"}
        found = {pattern.name: pattern.callback.geography_door for pattern in urls.urlpatterns}
        for name, mark in expected.items():
            self.assertEqual(found[name], mark, name)

    def test_nobody_signed_in_gets_nothing(self):
        from django.test import Client

        pin = self.pin()
        anonymous = Client()
        # Sent to sign in, or refused: a host's own login gate may answer
        # before the door does (401 for a request that asks for JSON).
        refused = (302, 401, 403)
        self.assertIn(anonymous.get(self.page_url).status_code, refused)
        self.assertIn(anonymous.get(self.url("pin_detail", pin)).status_code, refused)
        self.assertIn(anonymous.get(reverse("geography:my_contributions")).status_code, refused)
        self.assertIn(post(anonymous, self.url("pin_create", community=self.guild),
                           {**PIN, "op": op()}).status_code, refused)
        self.assertIn(anonymous.post(self.url("pin_comment_add", pin),
                                     {"body": "x", "op": op()}).status_code, refused)
        self.assertIn(anonymous.post(self.url("my_pin_delete", pin)).status_code, refused)
        self.assertEqual(CommunityPin.objects.count(), 1)
        self.assertNotIn("Well", anonymous.get(self.page_url).content.decode())


class VisibleToTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.guild_pin = self.pin()
        self.guild_zone = self.zone()
        self.other_pin = self.pin(self.other_user, self.other, name="Mill")
        self.other_zone = self.zone(self.other_user, self.other, name="Orchard")

    def uids(self, user):
        visible = access.visible_to(user)
        return ({str(row.uid) for row in visible.pins()},
                {str(row.uid) for row in visible.zones()})

    def test_a_member_sees_their_communities_rows_only(self):
        for user in (self.member_user, self.head_user, self.senior_user):
            with self.subTest(user=user.username):
                self.assertEqual(self.uids(user), ({str(self.guild_pin.uid)},
                                                   {str(self.guild_zone.uid)}))
        self.assertEqual(self.uids(self.other_user), ({str(self.other_pin.uid)},
                                                      {str(self.other_zone.uid)}))

    def test_staff_alone_sees_no_contribution(self):
        self.assertEqual(self.uids(self.staff_user), (set(), set()))

    def test_an_administrator_sees_every_community_s(self):
        pins, zones = self.uids(self.root)
        self.assertEqual(pins, {str(self.guild_pin.uid), str(self.other_pin.uid)})
        self.assertEqual(zones, {str(self.guild_zone.uid), str(self.other_zone.uid)})

    def test_a_hidden_row_is_for_its_author_and_the_moderators(self):
        CommunityPin.objects.filter(pk=self.guild_pin.pk).update(
            hidden_at=self.guild_pin.created_at)
        uid = {str(self.guild_pin.uid)}
        self.assertEqual(self.uids(self.member_user)[0], uid)       # the author
        self.assertEqual(self.uids(self.head_user)[0], uid)         # the head
        self.assertEqual(self.uids(self.senior_user)[0], set())     # a senior member: no
        self.assertIn(str(self.guild_pin.uid), self.uids(self.root)[0])

    def test_a_person_s_point_follows_visible_point(self):
        from toto.geography import saves

        shy_user, shy = member("shy")
        open_user, open_person = member("opal")
        open_person.show_address = True
        open_person.save(update_fields=["show_address"])
        saves.save_person_point(shy_user, shy, lat=50.0, lng=19.9, name="Home", note="n",
                                op=op())
        saves.save_person_point(open_user, open_person, lat=51.1, lng=17.0, name="Flat",
                                note="private note", op=op())
        for viewer in (self.member_user, shy_user, open_user, self.staff_user, self.root):
            with self.subTest(viewer=viewer.username):
                listed = {link.person_id for link in access.visible_to(viewer).people()}
                by_rule = {person.pk for person in (shy, open_person)
                           if access.visible_point(viewer, person) is not None}
                self.assertEqual(listed, by_rule)
        # And the page holds exactly that, with another person's note left out.
        rows = rows_of(client_of(self.member_user).get(self.page_url))
        people = [row for row in rows if row["kind"] == "person"]
        self.assertEqual([row["name"] for row in people], ["Opal"])
        self.assertEqual(people[0]["note"], "")
        self.assertNotIn("50.0", json.dumps(rows))
        own = [row for row in rows_of(client_of(shy_user).get(self.page_url))
               if row["kind"] == "person" and row.get("own")]
        self.assertEqual([(row["name"], row["note"]) for row in own], [("Shy", "n")])

    def test_the_page_holds_what_visible_to_holds_and_nothing_else(self):
        text = client_of(self.member_user).get(self.page_url).content.decode()
        self.assertIn(str(self.guild_pin.uid), text)
        self.assertIn(str(self.guild_zone.uid), text)
        for hidden in (str(self.other_pin.uid), str(self.other_zone.uid), "Mill", "Orchard"):
            self.assertNotIn(hidden, text)
        stranger = client_of(self.staff_user).get(self.page_url).content.decode()
        for hidden in (str(self.guild_pin.uid), "Well", "Meadow", "Mill"):
            self.assertNotIn(hidden, stranger)

    def test_headquarters_are_for_every_signed_in_member(self):
        from toto.geography import saves

        saves.save_headquarters(self.other_user, self.other, lat=54.3, lng=18.6,
                                name="Other house", note="", op=op())
        rows = rows_of(client_of(self.member_user).get(self.page_url))
        self.assertIn("Other house", [row["name"] for row in rows
                                      if row["kind"] == "headquarters"])


class DoorTests(LocationsCase):
    def setUp(self):
        super().setUp()
        self.guild_pin = self.pin()
        self.guild_zone = self.zone()
        self.other_pin = self.pin(self.other_user, self.other, name="Mill")
        self.other_zone = self.zone(self.other_user, self.other, name="Orchard")

    def test_who_creates(self):
        for user, status in ((self.member_user, 200), (self.head_user, 200),
                             (self.senior_user, 200), (self.root, 200),
                             (self.other_user, 403), (self.staff_user, 403)):
            with self.subTest(user=user.username):
                self.assertEqual(self.save_pin(user).status_code, status)
                self.assertEqual(self.save_zone(user).status_code, status)
        self.assertEqual(self.save_pin(community=type("C", (), {"slug": "no-such"})).status_code,
                         404)

    def test_another_community_s_uid_under_this_slug_is_404(self):
        for user in (self.member_user, self.head_user):
            client = client_of(user)
            for name, row in (("pin_detail", self.other_pin), ("zone_detail", self.other_zone)):
                with self.subTest(user=user.username, door=name):
                    url = self.url(name, row, community=self.guild)
                    self.assertEqual(client.get(url).status_code, 404)
                    self.assertEqual(post(client, url, {**PIN, **AREA, "op": op()}).status_code,
                                     404)
            for name, row in (("pin_delete", self.other_pin), ("zone_hide", self.other_zone),
                              ("zone_restore", self.other_zone)):
                self.assertEqual(
                    post(client, self.url(name, row, community=self.guild), {}).status_code, 404)
        self.assertEqual(CommunityPin.objects.count(), 2)
        self.assertEqual(CommunityZone.objects.count(), 2)

    def test_a_stranger_is_refused_with_403_at_every_door_of_a_row(self):
        for user in (self.other_user, self.staff_user):
            client = client_of(user)
            with self.subTest(user=user.username):
                self.assertEqual(client.get(self.url("pin_detail", self.guild_pin)).status_code,
                                 403)
                self.assertEqual(client.get(self.url("zone_detail", self.guild_zone)).status_code,
                                 403)
                for name in ("pin_detail", "pin_delete", "pin_hide", "pin_restore"):
                    self.assertEqual(
                        post(client, self.url(name, self.guild_pin), {"op": op()}).status_code,
                        403, name)
                self.assertEqual(self.comment(user, self.guild_pin).status_code, 403)
                self.assertEqual(self.comment(user, self.guild_zone,
                                              name="zone_comment_add").status_code, 403)
        self.assertEqual(CommunityPin.objects.count(), 2)

    def test_a_hidden_row_is_404_for_a_plain_member(self):
        CommunityPin.objects.filter(pk=self.guild_pin.pk).update(
            hidden_at=self.guild_pin.created_at)
        client = client_of(self.senior_user)
        self.assertEqual(client.get(self.url("pin_detail", self.guild_pin)).status_code, 404)
        self.assertEqual(self.comment(self.senior_user, self.guild_pin).status_code, 404)
        self.assertEqual(client_of(self.member_user).get(
            self.url("pin_detail", self.guild_pin)).status_code, 200)
        self.assertEqual(client_of(self.head_user).get(
            self.url("pin_detail", self.guild_pin)).status_code, 200)

    def test_only_the_author_edits(self):
        url = self.url("pin_detail", self.guild_pin)
        for user in (self.head_user, self.senior_user, self.root):
            with self.subTest(user=user.username):
                self.assertEqual(post(client_of(user), url,
                                      {**PIN, "name": "Theirs", "op": op()}).status_code, 403)
        self.guild_pin.address.refresh_from_db()
        self.assertEqual(self.guild_pin.address.name, "Well")
        self.assertEqual(post(client_of(self.member_user), url,
                              {**PIN, "name": "Mine", "op": op()}).status_code, 200)

    def test_an_unknown_uid_is_404(self):
        import uuid

        ghost = type("Row", (), {"uid": uuid.uuid4(), "community": self.guild})
        client = client_of(self.member_user)
        self.assertEqual(client.get(self.url("pin_detail", ghost)).status_code, 404)
        self.assertEqual(self.comment(self.member_user, ghost).status_code, 404)
