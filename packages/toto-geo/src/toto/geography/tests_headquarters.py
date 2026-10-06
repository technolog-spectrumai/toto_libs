"""A community's headquarters and zone (2026-10-06): set by its head or an
administrator, seen by every signed-in member, charged to whoever saves.

    manage.py test toto.geography.tests_headquarters
"""

import io
import json
import math
import re
from decimal import Decimal

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.geography import views
from toto.geography.models import Address, CommunityHeadquarters, GeographyUsageEvent, Zone
from toto.geography.testing import (BOWTIE, SQUARE, Economy, client_of, community, fresh_cache,
                                    member, no_funds, op, post, refusing_ledger, words_fields)

HQ = {"lat": 54.352, "lng": 18.6466, "name": "Harbour house", "note": "ring twice"}
AREA = {"name": "The harbour", "description": "from the pier to the gate", "outline": SQUARE}
MAP_MARKS = ("vendor/leaflet/leaflet.js", "data-geography-map", "geography-headquarters-config")
CONFIG = re.compile(
    r'<script id="geography-headquarters-config" type="application/json">(.*?)</script>', re.S)


def ring(n):
    return [[52 + 0.1 * math.sin(2 * math.pi * i / n), 21 + 0.1 * math.cos(2 * math.pi * i / n)]
            for i in range(n)]


class HeadquartersCase(TestCase):
    billed = False

    def setUp(self):
        fresh_cache(self)
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.economy = Economy(self) if self.billed else None
        self.head_user, self.head = member("hugo")
        self.member_user, self.member = member("mia")
        self.senior_user, self.senior = member("sen")
        self.staff_user, _staff = member("stef", is_staff=True)
        self.guild = community("Guild", head=self.head)
        self.other = community("Other", head=self.member)
        self.member.communities.add(self.guild)
        self.guild.senior_members.add(self.senior)
        self.root = type(self.head_user).objects.create_superuser("root", "r@example.test", "pw")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())
            self.root = type(self.root).objects.get(pk=self.root.pk)
        self.urls = {name: reverse(f"geography:{name}", kwargs={"slug": self.guild.slug})
                     for name in ("headquarters", "headquarters_clear", "zone", "zone_clear")}
        self.page_url = reverse("socialhub:community_detail", kwargs={"slug": self.guild.slug})

    def save_hq(self, user=None, slug=None, **changes):
        url = self.urls["headquarters"] if slug is None else reverse(
            "geography:headquarters", kwargs={"slug": slug})
        return post(client_of(user or self.head_user), url, {**HQ, "op": op(), **changes})

    def save_zone(self, user=None, **changes):
        return post(client_of(user or self.head_user), self.urls["zone"],
                    {**AREA, "op": op(), **changes})

    def page(self, user):
        response = client_of(user).get(self.page_url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def events(self, metric=None):
        rows = GeographyUsageEvent.objects.all()
        return rows.filter(metric_code=metric) if metric else rows


class WhoTests(HeadquartersCase):
    def test_the_head_saves_both(self):
        response = self.save_hq()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json(), {"headquarters": HQ, "charged": True})
        response = self.save_zone()
        self.assertEqual(response.json(), {"zone": AREA, "charged": True})
        link = CommunityHeadquarters.objects.get()
        self.assertEqual((link.community, link.address.name, link.zone.name),
                         (self.guild, "Harbour house", "The harbour"))
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 1))
        self.assertTrue(link.zone.outline.valid)
        self.assertEqual(link.zone.outline.num_interior_rings, 0)

    def test_an_administrator_saves_too(self):
        self.assertEqual(self.save_hq(self.root).status_code, 200)
        self.assertEqual(self.save_zone(self.root).status_code, 200)

    def test_a_member_a_senior_member_and_staff_are_refused_with_403(self):
        for user in (self.member_user, self.senior_user, self.staff_user):
            with self.subTest(user=user.username):
                for response in (self.save_hq(user), self.save_zone(user),
                                 post(client_of(user), self.urls["headquarters_clear"], {}),
                                 post(client_of(user), self.urls["zone_clear"], {})):
                    self.assertEqual(response.status_code, 403)
                    self.assertIn("error", response.json())
        self.assertFalse(CommunityHeadquarters.objects.exists())
        self.assertFalse(self.events().exists())

    def test_the_head_of_one_community_decides_nothing_for_another(self):
        # mia heads Other and is a plain member of Guild.
        self.save_hq()
        self.save_zone()
        mia = client_of(self.member_user)
        for name in ("headquarters_clear", "zone_clear"):
            self.assertEqual(post(mia, self.urls[name], {}).status_code, 403)
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 1))
        # Her own community's slug clears her own community's rows only.
        other = {name: reverse(f"geography:{name}", kwargs={"slug": self.other.slug})
                 for name in ("headquarters_clear", "zone_clear")}
        self.assertEqual(post(mia, other["headquarters_clear"], {}).json(), {"removed": False})
        self.assertEqual(post(mia, other["zone_clear"], {}).json(), {"removed": False})
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 1))
        self.assertEqual(CommunityHeadquarters.objects.get().community, self.guild)

    def test_an_unknown_slug_is_404_and_get_is_405(self):
        self.assertEqual(self.save_hq(slug="no-such-community").status_code, 404)
        self.assertEqual(client_of(self.head_user).get(self.urls["zone"]).status_code, 405)

    def test_every_door_carries_its_mark(self):
        for name in ("headquarters", "headquarters_clear", "zone", "zone_clear"):
            self.assertEqual(getattr(views, name).geography_door, "moderator")
        for name in ("search", "route", "my_address", "my_address_clear"):
            self.assertEqual(getattr(views, name).geography_door, "signed-in")
        from toto.geography import urls

        # Stage 64 added the Locations app's routes, with three more marks
        # (toto.geography.tests_access walks them by name).
        for pattern in urls.urlpatterns:
            self.assertIn(getattr(pattern.callback, "geography_door", None),
                          ("signed-in", "moderator", "community", "contribution", "author"),
                          pattern.name)


class SaveTests(HeadquartersCase):
    def test_saving_never_writes_the_seat(self):
        type(self.guild).objects.filter(pk=self.guild.pk).update(seat="Długi Targ 1, Gdańsk")
        self.save_hq()
        self.save_zone()
        self.guild.refresh_from_db()
        self.assertEqual(self.guild.seat, "Długi Targ 1, Gdańsk")
        self.assertEqual(Address.objects.get().postal_address, "")

    def test_an_invalid_outline_is_refused_and_charges_nothing(self):
        bad = (BOWTIE, SQUARE[:2], [], None, "square", [[52, 21], [52, 21], [52.1, 21]],
               [[0, 0], [0, 1], [0, 2]], [[91, 0], [0, 0], [0, 1]], [[1, 2, 3]] * 4)
        for outline in bad:
            with self.subTest(outline=outline):
                response = self.save_zone(outline=outline)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        self.assertFalse(Zone.objects.exists())
        self.assertFalse(self.events().exists())

    def test_input_no_database_takes_is_400_as_json_and_free(self):
        huge = 10 ** 400
        calls = (
            lambda: self.save_hq(lat=huge), lambda: self.save_hq(name="a\x00b"),
            lambda: self.save_hq(note="\ud800"),
            lambda: self.save_zone(outline=[[52, 21], [52, huge], [52.1, 21.1]]),
            lambda: self.save_zone(name="a\x00b"), lambda: self.save_zone(description="a\x00b"),
            lambda: self.save_zone(name="\udc00"), lambda: self.save_zone(description="a\ud800"),
        )
        for index, call in enumerate(calls):
            with self.subTest(call=index):
                response = call()
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(set(response.json()), {"error"})
        self.assertFalse(CommunityHeadquarters.objects.exists())
        self.assertFalse(Address.objects.exists() or Zone.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_body_nested_past_python_s_depth_is_400_on_every_door(self):
        """``json.loads`` of 100,000 open brackets is a RecursionError, which
        is no ValueError: every door used to answer it with a 500."""
        doors = [reverse("geography:search"), reverse("geography:route"),
                 reverse("geography:my_address"), reverse("geography:my_address_clear"),
                 *self.urls.values()]
        self.assertEqual(len(doors), 8)
        head = client_of(self.head_user)
        for url in doors:
            for body in ("[" * 100000, '{"a":' * 50000):
                with self.subTest(url=url, body=body[:5]):
                    response = head.post(url, data=body, content_type="application/json")
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response["Cache-Control"], "no-store")
                    self.assertEqual(set(response.json()), {"error"})
        self.assertFalse(self.events().exists())

    def test_five_hundred_corners_are_saved_and_501_refused(self):
        self.assertEqual(self.save_zone(outline=ring(501)).status_code, 400)
        self.assertFalse(Zone.objects.exists())
        self.assertFalse(self.events().exists())
        self.assertEqual(self.save_zone(outline=ring(500)).status_code, 200)
        self.assertEqual(len(Zone.objects.get().outline[0]) - 1, 500)

    def test_first_a_pin_and_a_zone_then_notes(self):
        self.save_hq()
        self.save_zone()
        self.save_hq(note="ring three times")
        self.save_zone(outline=SQUARE[:3])
        self.assertEqual([event.metric_code for event in self.events().order_by("pk")],
                         ["geography.pin", "geography.zone", "geography.note", "geography.note"])
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (1, 1))
        self.assertEqual(set(self.events().values_list("user", flat=True)), {self.head_user.pk})

    def test_whoever_saves_pays(self):
        self.save_hq(self.root)
        self.assertEqual(self.events().get().user, self.root)

    def test_the_same_op_twice_is_one_charge_and_another_body_409(self):
        key = op()
        first = self.save_hq(op=key)
        second = self.save_hq(op=key)
        self.assertEqual((first.json()["charged"], second.json()["charged"]), (True, False))
        self.assertEqual(self.save_hq(op=key, name="Elsewhere").status_code, 409)
        zone_key = op()
        self.save_zone(op=zone_key)
        self.assertFalse(self.save_zone(op=zone_key).json()["charged"])
        self.assertEqual(self.save_zone(op=zone_key, outline=SQUARE[:3]).status_code, 409)
        self.assertEqual(self.events().count(), 2)
        self.assertEqual(Address.objects.get().name, "Harbour house")

    def test_an_op_bought_for_the_point_cannot_save_the_zone(self):
        key = op()
        self.save_hq(op=key)
        self.assertEqual(self.save_zone(op=key).status_code, 409)
        self.assertFalse(Zone.objects.exists())

    def test_an_unchanged_save_is_free(self):
        self.save_hq()
        self.save_zone()
        self.assertEqual(self.save_hq().json(), {"headquarters": HQ, "charged": False})
        self.assertEqual(self.save_zone().json(), {"zone": AREA, "charged": False})
        self.assertEqual(self.events().count(), 2)

    def test_402_before_and_after_leaves_nothing(self):
        for refusing in (no_funds, refusing_ledger):
            with self.subTest(refusing=refusing.__name__), refusing():
                self.assertEqual(self.save_hq().status_code, 402)
                self.assertEqual(self.save_zone().status_code, 402)
        self.assertFalse(CommunityHeadquarters.objects.exists())
        self.assertFalse(Address.objects.exists())
        self.assertFalse(Zone.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_ledger_refusal_on_a_change_keeps_the_old_zone(self):
        self.save_zone()
        with refusing_ledger():
            self.assertEqual(self.save_zone(outline=SQUARE[:3], name="Smaller").status_code, 402)
        zone = Zone.objects.get()
        self.assertEqual((zone.name, len(zone.outline[0]) - 1), ("The harbour", 4))

    def test_remove_is_free_and_deletes_the_rows_themselves(self):
        self.save_hq()
        self.save_zone()
        head = client_of(self.head_user)
        self.assertEqual(post(head, self.urls["headquarters_clear"], {}).json(), {"removed": True})
        self.assertEqual((Address.objects.count(), Zone.objects.count()), (0, 1))
        self.assertEqual(post(head, self.urls["zone_clear"], {}).json(), {"removed": True})
        self.assertEqual(Zone.objects.count(), 0)
        self.assertFalse(CommunityHeadquarters.objects.exists())
        self.assertEqual(self.events().count(), 2)
        self.assertEqual(post(head, self.urls["zone_clear"], {}).json(), {"removed": False})


class PageTests(HeadquartersCase):
    def config(self, html):
        return json.loads(CONFIG.search(html).group(1))

    def test_the_page_draws_nothing_when_neither_is_set(self):
        for user in (self.member_user, self.senior_user, self.staff_user):
            html = self.page(user)
            for mark in MAP_MARKS + ("geography-headquarters-section", "leaflet", "geography/"):
                self.assertNotIn(mark, html)

    def test_the_head_gets_the_forms_before_anything_is_set(self):
        html = self.page(self.head_user)
        for mark in MAP_MARKS + ('data-geo="save-point"', 'data-geo="save-zone"',
                                 "geography/zone_draw.js"):
            self.assertIn(mark, html)
        config = self.config(html)
        self.assertEqual((config["points"], config["zone"]), ([], None))
        self.assertEqual(config["urls"]["savePoint"], self.urls["headquarters"])
        self.assertEqual(config["urls"]["saveZone"], self.urls["zone"])
        self.assertEqual(config["urls"]["clearZone"], self.urls["zone_clear"])

    def test_every_signed_in_member_sees_the_pin_and_the_outline_and_no_form(self):
        self.save_hq()
        self.save_zone()
        stranger_user, _stranger = member("stan")
        for user in (self.member_user, stranger_user, self.staff_user):
            with self.subTest(user=user.username):
                html = self.page(user)
                for mark in MAP_MARKS + ('data-geo="search-go"',):
                    self.assertIn(mark, html)
                # Route search is the Locations page's alone (2026-10-06).
                for mark in ('data-geo="route', 'data-geo="from"', 'data-geo="to"',
                             "geography-route-go", "Find route",
                             reverse("geography:route")):
                    self.assertNotIn(mark, html)
                config = self.config(html)
                self.assertEqual(config["points"][0]["kind"], "headquarters")
                self.assertEqual((config["points"][0]["lat"], config["points"][0]["label"]),
                                 (54.352, "Harbour house"))
                self.assertEqual(config["zone"]["outline"], SQUARE)
                self.assertFalse(config["can_edit_point"] or config["can_edit_zone"])
                self.assertEqual(set(config["urls"]), {"search"})
                for mark in ('data-geo="save-point"', 'data-geo="save-zone"',
                             'data-geo="clear-zone"', "geography/zone_draw.js"):
                    self.assertNotIn(mark, html)

    def test_the_page_s_data_is_the_pin_and_the_outline_and_the_texts_are_the_head_s_form(self):
        """The headquarters' note and the zone's name and description are
        drawn for nobody on the map: they are in no page's data, and in the
        page of nobody but whoever may set them, whose form holds them."""
        self.save_hq()
        self.save_zone()
        texts = (HQ["note"], AREA["name"], AREA["description"])
        for user in (self.member_user, self.senior_user, self.staff_user, self.head_user,
                     self.root):
            with self.subTest(user=user.username):
                html = self.page(user)
                config = self.config(html)
                self.assertEqual(config["points"], [{"kind": "headquarters", "lat": 54.352,
                                                     "lng": 18.6466, "label": "Harbour house"}])
                self.assertEqual(config["zone"], {"outline": SQUARE})
                for text in texts:
                    self.assertNotIn(text, CONFIG.search(html).group(1))
                    self.assertEqual(html.count(text),
                                     1 if user in (self.head_user, self.root) else 0, text)

    def test_the_words_are_typed_in_the_two_dialogs_and_nowhere_else(self):
        """The owner, 2026-10-06: "the information like name etc should be
        inputed via modal and modal only". For the head, before anything is
        set and after: no field for a name, a note or a description beside
        or under the map; the point's dialog and the zone's hold them. A
        member's page has neither field nor dialog."""
        for saved in (False, True):
            if saved:
                self.save_hq()
                self.save_zone()
            with self.subTest(saved=saved):
                html = self.page(self.head_user)
                outside, inside = words_fields(html, "data-geography-map")
                self.assertEqual(outside, [])
                self.assertEqual(inside, ["point-name", "point-note-text", "zone-name",
                                          "zone-description"])
                section = html[html.index('id="geography-headquarters-section"'):]
                section = section[:section.index("</section>")]
                self.assertEqual(section.count('role="dialog"'), 2)
                self.assertEqual(section.count('x-trap="open"'), 2)
                for tool, dialog, marks in (
                        ("edit-point", "point-dialog", ("point-continue", "close-point")),
                        ("edit-zone", "zone-dialog", ("zone-undo", "zone-restart",
                                                      "zone-continue", "close-zone"))):
                    beside = section[section.index(f'data-geo="{tool}"'):
                                     section.index(f'data-geo="{dialog}"')]
                    for mark in marks:
                        self.assertIn(f'data-geo="{mark}"', beside)
                    self.assertNotIn("<input", beside)
                    self.assertNotIn("<textarea", beside)
                    self.assertEqual(f'data-geo="clear-{tool[5:]}"' in beside, saved)
        member_page = self.page(self.member_user)
        self.assertIn("data-geography-map", member_page)
        self.assertEqual(words_fields(member_page, "data-geography-map"), ([], []))
        self.assertNotIn('role="dialog"', member_page[
            member_page.index('id="geography-headquarters-section"'):])

    def test_the_form_has_no_postal_field(self):
        html = self.page(self.head_user)
        section = html[html.index('id="geography-headquarters-section"'):]
        section = section[:section.index("</section>")]
        self.assertNotIn('name="seat"', section)
        self.assertNotIn("postal_address", section)

    def test_another_community_s_page_shows_nothing_of_this_one(self):
        self.save_hq()
        html = client_of(self.head_user).get(reverse(
            "socialhub:community_detail", kwargs={"slug": self.other.slug})).content.decode()
        self.assertNotIn("54.352", html)
        self.assertNotIn("Harbour house", html)


class BilledTests(HeadquartersCase):
    billed = True

    def test_a_headquarters_costs_a_pin_a_zone_one_and_a_change_a_note(self):
        before = self.economy.held(self.head_user, "storage")
        self.save_hq()
        self.save_zone()
        self.assertEqual(before - self.economy.held(self.head_user, "storage"), Decimal("1.5"))
        self.save_zone(description="wider")
        self.assertEqual(before - self.economy.held(self.head_user, "storage"), Decimal("1.7"))
        self.assertEqual((self.economy.charges("geography.pin"),
                          self.economy.charges("geography.zone"),
                          self.economy.charges("geography.note")), (1, 1, 1))

    def test_an_empty_pool_refuses_the_zone_with_the_mana_sentence(self):
        self.economy.empty(self.head_user, "storage")
        response = self.save_zone()
        self.assertEqual(response.status_code, 402)
        self.assertIn("storage mana", response.json()["error"])
        self.assertFalse(Zone.objects.exists())

    def test_the_head_s_page_names_the_prices(self):
        from django.core.cache import cache

        cache.clear()
        html = self.page(self.head_user)
        section = html[html.index('id="geography-headquarters-section"'):]
        section = section[:section.index("</section>")]
        # A search, the first headquarters, the first zone. No route: that
        # is the Locations page's alone (2026-10-06).
        self.assertEqual(re.findall(r'data-mana-role="(\w+)"', section),
                         ["compute", "storage", "storage"])
