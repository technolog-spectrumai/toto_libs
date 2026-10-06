"""A person's point on their profile (2026-10-06): who sees it, what the
page holds, and what saving costs.

    manage.py test toto.geography.tests_profile
"""

import io
import json
import re
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.geography import access
from toto.geography.models import Address, GeographyUsageEvent, PersonAddress
from toto.geography.testing import (Economy, client_of, fresh_cache, member, no_funds, op, post,
                                    refusing_ledger)

HOME = {"lat": 52.2297, "lng": 21.0122, "name": "Home", "note": "third floor"}
MAP_MARKS = ("vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css", "data-geography-map",
             "geography/map.js", "geography-address-config", "totoTileLayer")
CONFIG = re.compile(r'<script id="geography-address-config" type="application/json">(.*?)</script>',
                    re.S)


class ProfileCase(TestCase):
    billed = False

    def setUp(self):
        fresh_cache(self)
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.economy = Economy(self) if self.billed else None
        self.ada_user, self.ada = member("ada")
        self.bob_user, self.bob = member("bob")
        self.root = type(self.ada_user).objects.create_superuser("root", "r@example.test", "pw")
        if apps.is_installed("toto.subscriptions"):
            call_command("bootstrap_plans", stdout=io.StringIO())   # root → the Superuser plan
            self.root = type(self.root).objects.get(pk=self.root.pk)
        self.url = reverse("geography:my_address")
        self.clear_url = reverse("geography:my_address_clear")
        self.page_url = reverse("socialhub:profile_details", args=[self.ada.slug])

    def save(self, client=None, **changes):
        return post(client or client_of(self.ada_user), self.url, {**HOME, "op": op(), **changes})

    def page(self, user):
        response = client_of(user).get(self.page_url)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def config(self, html):
        return json.loads(CONFIG.search(html).group(1))

    def events(self, metric=None):
        rows = GeographyUsageEvent.objects.all()
        return rows.filter(metric_code=metric) if metric else rows


class VisibilityTests(ProfileCase):
    def test_the_owner_sees_the_map_and_the_form_before_any_point(self):
        html = self.page(self.ada_user)
        for mark in MAP_MARKS:
            self.assertIn(mark, html)
        config = self.config(html)
        self.assertEqual(config["points"], [])
        self.assertTrue(config["can_edit_point"])
        self.assertFalse(config["can_edit_zone"])
        self.assertEqual(config["urls"]["savePoint"], self.url)
        self.assertEqual(config["urls"]["clearPoint"], self.clear_url)
        self.assertIn("You have no point on the map yet.", html)
        self.assertIn('data-geo="save-point"', html)
        self.assertNotIn('data-geo="clear-point"', html)
        self.assertNotIn('data-geo="save-zone"', html)

    def test_the_owner_sees_their_point_with_the_switch_off(self):
        self.save()
        html = self.page(self.ada_user)
        point = self.config(html)["points"][0]
        self.assertEqual((point["kind"], point["lat"], point["lng"], point["label"]),
                         ("address", 52.2297, 21.0122, "Home"))
        self.assertIn("Hidden from other members.", html)
        self.assertIn('data-geo="clear-point"', html)

    def test_a_visitor_with_the_switch_off_gets_no_map_and_no_coordinate(self):
        self.save()
        self.assertFalse(self.ada.show_address)
        html = self.page(self.bob_user)
        for mark in MAP_MARKS + ("52.2297", "21.0122", "third floor", "geography-address-section",
                                 "leaflet", "geography/"):
            self.assertNotIn(mark, html)
        self.assertIsNone(access.visible_point(self.bob_user, self.ada))

    def test_a_visitor_with_the_switch_on_sees_the_map_and_no_form(self):
        self.save()
        type(self.ada).objects.filter(pk=self.ada.pk).update(show_address=True)
        html = self.page(self.bob_user)
        for mark in MAP_MARKS:
            self.assertIn(mark, html)
        config = self.config(html)
        self.assertEqual(config["points"][0]["lat"], 52.2297)
        self.assertFalse(config["can_edit_point"])
        self.assertNotIn("savePoint", config["urls"])
        self.assertNotIn("clearPoint", config["urls"])
        for mark in ('data-geo="save-point"', 'data-geo="clear-point"', 'data-geo="open-point"'):
            self.assertNotIn(mark, html)

    def test_a_visitor_sees_no_section_where_there_is_no_point(self):
        type(self.ada).objects.filter(pk=self.ada.pk).update(show_address=True)
        html = self.page(self.bob_user)
        for mark in MAP_MARKS:
            self.assertNotIn(mark, html)

    def test_an_administrator_sees_a_hidden_point_and_cannot_set_it(self):
        from toto.socialhub.contact_access import is_administrator

        self.save()
        self.assertTrue(is_administrator(self.root))
        html = self.page(self.root)
        config = self.config(html)
        self.assertEqual(config["points"][0]["lat"], 52.2297)
        self.assertFalse(config["can_edit_point"])

    def test_one_function_answers_for_the_page_and_the_doors(self):
        self.save()
        self.assertEqual(access.visible_point(self.ada_user, self.ada).name, "Home")
        self.assertIsNone(access.visible_point(self.bob_user, self.ada))
        self.assertIsNotNone(access.visible_point(self.root, self.ada))
        from django.contrib.auth.models import AnonymousUser

        type(self.ada).objects.filter(pk=self.ada.pk).update(show_address=True)
        self.ada.refresh_from_db()
        self.assertIsNotNone(access.visible_point(self.bob_user, self.ada))
        self.assertIsNone(access.visible_point(AnonymousUser(), self.ada))

    def test_the_page_that_draws_the_map_has_the_search_the_route_panel_and_both_hints(self):
        Economy(self)
        from django.core.cache import cache

        cache.clear()       # the rate card is read through the cache
        html = self.page(self.ada_user)
        for mark in ('data-geo="q"', 'data-geo="search-go"', 'data-geo="route-go"',
                     'data-geo="from"', 'data-geo="to"', 'data-geo="mode"',
                     '<option value="car">', '<option value="bicycle">', '<option value="foot">'):
            self.assertIn(mark, html)
        self.assertNotIn("public_transport", html)
        config = self.config(html)
        self.assertEqual(config["urls"]["search"], reverse("geography:search"))
        self.assertEqual(config["urls"]["route"], reverse("geography:route"))
        # The three prices: a search (0.5), a route (1), the first save (0.5).
        section = html[html.index('id="geography-address-section"'):]
        section = section[:section.index("</section>")]
        self.assertEqual(re.findall(r'data-mana-role="(\w+)"', section),
                         ["compute", "compute", "storage"],
                         "a price beside Search, Find route and Save")

    @override_settings(LOCATIONS_GEOCODING={"enabled": False},
                       GEOGRAPHY_ROUTING={"enabled": False})
    def test_a_host_with_both_services_off_draws_neither_control(self):
        html = self.page(self.ada_user)
        self.assertIn("data-geography-map", html)
        self.assertNotIn('data-geo="search-go"', html)
        self.assertNotIn('data-geo="route-go"', html)
        self.assertEqual(set(self.config(html)["urls"]), {"savePoint", "clearPoint"})

    def test_the_page_shows_one_address_text_and_the_form_has_no_postal_field(self):
        type(self.ada).objects.filter(pk=self.ada.pk).update(address="Marszałkowska 1\nWarszawa")
        self.save()
        html = self.page(self.ada_user)
        self.assertEqual(html.count("Marszałkowska 1"), 1)
        section = html[html.index('id="geography-address-section"'):]
        section = section[:section.index("</section>")]
        self.assertNotIn("postal", section.lower().replace("your postal address stays", ""))
        self.assertNotIn('name="address"', section)


class SaveTests(ProfileCase):
    def test_saving_makes_one_address_and_one_link_and_never_writes_the_text(self):
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response.json(), {"address": HOME, "charged": True})
        link = PersonAddress.objects.get()
        self.assertEqual((link.person, link.address.name, link.address.created_by),
                         (self.ada, "Home", self.ada_user))
        self.assertEqual(Address.objects.count(), 1)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.address or "", "")
        self.assertEqual(link.address.postal_address, "")

    def test_first_a_pin_then_a_note(self):
        self.save()
        self.save(name="Home, new door")
        self.assertEqual([event.metric_code for event in self.events().order_by("pk")],
                         ["geography.pin", "geography.note"])
        self.assertEqual(Address.objects.count(), 1, "a change keeps the one row")
        self.assertEqual(Address.objects.get().name, "Home, new door")

    def test_the_same_op_twice_is_one_charge(self):
        key = op()
        client = client_of(self.ada_user)
        first = self.save(client, op=key)
        second = self.save(client, op=key)
        self.assertEqual((first.json()["charged"], second.json()["charged"]), (True, False))
        self.assertEqual(second.json()["address"], HOME)
        self.assertEqual(self.events().count(), 1)

    def test_the_same_op_with_another_point_is_409_and_changes_nothing(self):
        key = op()
        self.save(op=key)
        response = self.save(op=key, lat=50.0)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Address.objects.get().point.y, 52.2297)
        self.assertEqual(self.events().count(), 1)

    def test_two_changes_at_once_under_one_op_the_second_is_409(self):
        """Two changes sent at once under one op, each with another note:
        the second passed its first check before the first one's event was
        there (``known`` is patched to answer as that race does). Its rows
        are undone and it is told 409; the first one's point stands."""
        self.save()
        key = op()
        self.assertTrue(self.save(op=key, note="fourth floor").json()["charged"])
        with mock.patch("toto.geography.charging.known", return_value=False), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            response = self.save(op=key, note="fifth floor")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(set(response.json()), {"error"})
        charged_again.assert_not_called()
        self.assertEqual(Address.objects.get().note, "fourth floor")
        self.assertEqual(self.events().count(), 2)

    def test_the_same_change_twice_at_once_is_answered_as_a_replay(self):
        self.save()
        key = op()
        self.save(op=key, note="fourth floor")
        with mock.patch("toto.geography.charging.known", return_value=False), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            response = self.save(op=key, note="fourth floor")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"address": {**HOME, "note": "fourth floor"},
                                           "charged": False})
        charged_again.assert_not_called()
        self.assertEqual(self.events().count(), 2)

    def test_an_unchanged_save_is_free(self):
        self.save()
        response = self.save()
        self.assertEqual(response.json(), {"address": HOME, "charged": False})
        self.assertEqual(self.events().count(), 1)

    def test_no_funds_is_402_and_leaves_no_point(self):
        with no_funds():
            response = self.save()
        self.assertEqual(response.status_code, 402)
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_ledger_refusal_leaves_no_point(self):
        with refusing_ledger():
            response = self.save()
        self.assertEqual(response.status_code, 402)
        self.assertNotIn("address", response.json())
        self.assertFalse(Address.objects.exists())
        self.assertFalse(PersonAddress.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_ledger_refusal_on_a_change_keeps_the_old_point(self):
        self.save()
        with refusing_ledger():
            response = self.save(lat=50.0, name="Elsewhere")
        self.assertEqual(response.status_code, 402)
        address = Address.objects.get()
        self.assertEqual((address.point.y, address.name), (52.2297, "Home"))
        self.assertEqual(self.events().count(), 1)

    def test_bad_input_is_400_and_free(self):
        for changes in ({"lat": 91}, {"lng": "21"}, {"lat": None}, {"name": "x" * 201},
                        {"note": "x" * 2001}, {"name": 7}, {"op": "nope"}, {"op": None}):
            with self.subTest(changes=changes):
                self.assertEqual(self.save(**changes).status_code, 400)
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_input_no_database_takes_is_400_as_json_and_free(self):
        """An integer past a float's range, a NUL (PostgreSQL refuses one
        in text; SQLite would keep it) and a lone surrogate, each of which
        JSON carries and each of which used to end in a 500."""
        hostile = ({"lat": 10 ** 400}, {"lng": -(10 ** 400)}, {"name": "a\x00b"},
                   {"note": "ring\x00twice"}, {"name": "a\ud800"}, {"note": "\udfff"})
        for changes in hostile:
            with self.subTest(changes=ascii(changes)[:40]):
                response = self.save(**changes)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertEqual(set(response.json()), {"error"})
        self.assertFalse(Address.objects.exists())
        self.assertFalse(self.events().exists())

    def test_a_note_keeps_its_line_breaks(self):
        self.assertEqual(self.save(note="third floor\nring twice\tthen wait").status_code, 200)
        self.assertEqual(Address.objects.get().note, "third floor\nring twice\tthen wait")

    def test_with_the_cache_down_the_page_loads_and_a_save_still_saves(self):
        """The limiter cannot count (Redis away: django-redis answers None).
        Search and routes refuse then (``tests_places``, ``tests_routes``);
        what asks no limiter and no provider goes on: the page that draws
        the map, a save, a removal."""
        with mock.patch("toto.core.ratelimit.cache.incr", return_value=None):
            self.assertEqual(self.save().json(), {"address": HOME, "charged": True})
            html = self.page(self.ada_user)
            self.assertEqual(self.config(html)["points"][0]["lat"], HOME["lat"])
            for mark in MAP_MARKS:
                self.assertIn(mark, html)
            self.assertTrue(post(client_of(self.ada_user), self.clear_url, {}).json()["removed"])
        self.assertEqual(self.events().count(), 1)
        self.assertFalse(Address.objects.exists())

    def test_get_is_405_and_a_member_saves_only_their_own(self):
        client = client_of(self.bob_user)
        self.assertEqual(client.get(self.url).status_code, 405)
        self.assertEqual(self.save(client, person=self.ada.pk, slug=self.ada.slug).status_code,
                         200)
        self.assertEqual(PersonAddress.objects.get().person, self.bob,
                         "the door has no way to name another person")

    def test_remove_is_free_and_deletes_the_address_itself(self):
        self.save()
        response = post(client_of(self.ada_user), self.clear_url, {})
        self.assertEqual(response.json(), {"removed": True})
        self.assertFalse(Address.objects.exists())
        self.assertFalse(PersonAddress.objects.exists())
        self.assertEqual(self.events().count(), 1, "only the first save was charged")
        self.assertEqual(post(client_of(self.ada_user), self.clear_url, {}).json(),
                         {"removed": False})

    def test_one_member_cannot_remove_another_s_point(self):
        self.save()
        post(client_of(self.bob_user), self.clear_url, {})
        self.assertTrue(PersonAddress.objects.filter(person=self.ada).exists())

    def test_a_point_saved_again_after_removal_is_a_pin_again(self):
        self.save()
        post(client_of(self.ada_user), self.clear_url, {})
        self.save()
        self.assertEqual(self.events("geography.pin").count(), 2)


class BilledSaveTests(ProfileCase):
    billed = True

    def test_a_first_save_costs_half_a_storage_mana_and_a_change_a_fifth(self):
        before = self.economy.held(self.ada_user, "storage")
        self.save()
        self.assertEqual(before - self.economy.held(self.ada_user, "storage"), Decimal("0.5"))
        self.save(note="fourth floor")
        self.assertEqual(before - self.economy.held(self.ada_user, "storage"), Decimal("0.7"))
        self.save(note="fourth floor")
        self.assertEqual(before - self.economy.held(self.ada_user, "storage"), Decimal("0.7"))
        self.assertEqual((self.economy.charges("geography.pin"),
                          self.economy.charges("geography.note")), (1, 1))

    def test_an_empty_storage_pool_refuses_with_the_mana_sentence(self):
        self.economy.empty(self.ada_user, "storage")
        response = self.save()
        self.assertEqual(response.status_code, 402)
        self.assertIn("storage mana", response.json()["error"])
        self.assertFalse(Address.objects.exists())
        self.assertEqual(self.economy.charges(), 0)

    def test_the_hint_beside_save_names_the_price_of_what_it_will_be(self):
        first = self.page(self.ada_user)
        section = first[first.index('data-geo="save-point"'):][:1500]
        self.assertIn("0.5", section)
        self.save()
        later = self.page(self.ada_user)
        section = later[later.index('data-geo="save-point"'):][:1500]
        self.assertIn("0.2", section)
