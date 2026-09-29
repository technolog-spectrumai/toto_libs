"""Routes and map layers kept to clearances (2026-09-29), rule by rule.

`tests_clearances` pins the headline: a hidden route is a missing one. These
walk the rule's corners — who reads (members, the creator or owner,
superusers; not staff, not a community), who chooses the clearances,
what `clearances_save` refuses, what the detail page's section shows to whom,
and that the map page and the two JSON doors agree with the per-object door.
"""

import json
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from toto.api.testutils import add_to_mesh
from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import (
    HAS_GIS, Address, MapLayer, MapLayerClearance, Route, RouteClearance, Territory,
)
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community

User = get_user_model()


def fresh(user):
    """The user re-read, so a Person made in the test is seen (the reverse
    one-to-one caches its absence on the instance)."""
    return User.objects.get(pk=user.pk)


@skipUnless(HAS_GIS, "routes and layers draw on geometry; the map 404s without GIS")
class ClearanceFixture(TestCase):
    """Two clearances and a plain community; readers of every kind; four
    routes (open, kept, kept to both clearances, kept with no creator) and four
    layers (open, kept with an owner, kept with none, inactive)."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.gis.geos import LineString, MultiLineString, Polygon

        from toto.locations.models import MapLayerPolygon

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.board = Clearance.objects.create(name="internal", slug="internal")
        cls.seniors = Clearance.objects.create(name="confidential", slug="confidential")
        cls.guild = Community.objects.create(name="guild", slug="guild")          # a community, not a clearance

        cls.creator = User.objects.create_user("creator", password="x")
        cls.member = User.objects.create_user("member", password="x")
        Person.objects.create(user=cls.member, display_name="Member").clearances.add(cls.board)
        cls.both = User.objects.create_user("both", password="x")
        Person.objects.create(user=cls.both, display_name="Both").clearances.add(
            cls.board, cls.seniors)
        cls.guildsman = User.objects.create_user("guildsman", password="x")
        Person.objects.create(user=cls.guildsman, display_name="Guildsman").communities.add(cls.guild)
        cls.stranger = User.objects.create_user("stranger", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@example.org", "x")
        cls.owner = User.objects.create_user("owner", password="x")
        cls.owner_person = Person.objects.create(user=cls.owner, display_name="Owner")

        def line(*points):
            return MultiLineString(LineString(*points))

        cls.open = Route.objects.create(name="OpenRoute", created_by=cls.creator,
                                        geometry=line((18.0, 54.0), (18.5, 54.5)))
        cls.kept = Route.objects.create(name="BoardRoute", created_by=cls.creator,
                                        geometry=line((19.0, 50.0), (19.5, 50.5)))
        RouteClearance.objects.create(route=cls.kept, clearance=cls.board)
        cls.double = Route.objects.create(name="DoubleRoute", created_by=cls.creator,
                                          geometry=line((20.0, 51.0), (20.5, 51.5)))
        RouteClearance.objects.create(route=cls.double, clearance=cls.board)
        RouteClearance.objects.create(route=cls.double, clearance=cls.seniors)
        cls.orphan = Route.objects.create(name="OrphanRoute",
                                          geometry=line((21.0, 52.0), (21.5, 52.5)))
        RouteClearance.objects.create(route=cls.orphan, clearance=cls.board)

        square = Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), srid=4326)
        cls.open_layer = MapLayer.objects.create(name="Open layer", slug="open-layer")
        cls.board_layer = MapLayer.objects.create(name="Board layer", slug="board-layer",
                                                  owner=cls.owner_person)
        MapLayerClearance.objects.create(layer=cls.board_layer, clearance=cls.board)
        cls.nobodys_layer = MapLayer.objects.create(name="Nobodys layer", slug="nobodys-layer")
        MapLayerClearance.objects.create(layer=cls.nobodys_layer, clearance=cls.board)
        cls.idle_layer = MapLayer.objects.create(name="Idle layer", slug="idle-layer",
                                                 is_active=False)
        for layer in (cls.open_layer, cls.board_layer, cls.nobodys_layer, cls.idle_layer):
            MapLayerPolygon.objects.create(layer=layer, geometry=square, center=square.centroid,
                                           value=1.0, name=f"{layer.name} cell")

        cls.readers = (cls.creator, cls.member, cls.both, cls.guildsman, cls.stranger,
                       cls.staff, cls.root, cls.owner)
        cls.routes = (cls.open, cls.kept, cls.double, cls.orphan)
        cls.layers = (cls.open_layer, cls.board_layer, cls.nobodys_layer, cls.idle_layer)

    @staticmethod
    def names(queryset):
        return set(queryset.values_list("name", flat=True))


class ReadingRuleTests(ClearanceFixture):
    def test_a_stranger_reads_only_the_open_routes(self):
        self.assertEqual(self.names(access.readable_routes(self.stranger)), {"OpenRoute"})

    def test_a_clearance_member_reads_the_routes_kept_to_their_clearance(self):
        self.assertEqual(self.names(access.readable_routes(self.member)),
                         {"OpenRoute", "BoardRoute", "DoubleRoute", "OrphanRoute"})

    def test_a_route_kept_to_two_clearances_is_listed_once_for_a_member_of_both(self):
        rows = list(access.readable_routes(self.both).values_list("name", flat=True))
        self.assertEqual(rows.count("DoubleRoute"), 1)

    def test_the_creator_reads_their_kept_routes_without_being_in_the_clearance(self):
        self.assertEqual(self.names(access.readable_routes(self.creator)),
                         {"OpenRoute", "BoardRoute", "DoubleRoute"})

    def test_staff_is_not_a_clearance(self):
        self.assertEqual(self.names(access.readable_routes(self.staff)), {"OpenRoute"})
        self.assertFalse(access.may_read(self.staff, self.kept))

    def test_a_superuser_reads_every_route_and_layer(self):
        self.assertEqual(access.readable_routes(self.root).count(), len(self.routes))
        self.assertEqual(access.readable_layers(self.root).count(), len(self.layers))

    def test_an_anonymous_visitor_reads_only_what_is_open(self):
        anonymous = AnonymousUser()
        self.assertEqual(self.names(access.readable_routes(anonymous)), {"OpenRoute"})
        self.assertEqual(self.names(access.readable_layers(anonymous)),
                         {"Open layer", "Idle layer"})
        self.assertFalse(access.may_read(anonymous, self.kept))
        self.assertTrue(access.may_read(anonymous, self.open))

    def test_the_given_queryset_is_narrowed_not_replaced(self):
        narrowed = access.readable_routes(self.member, Route.objects.filter(name__startswith="Board"))
        self.assertEqual(self.names(narrowed), {"BoardRoute"})
        self.assertEqual(self.names(access.readable_layers(
            self.member, MapLayer.objects.filter(is_active=False))), {"Idle layer"})

    def test_the_layer_owner_reads_their_kept_layer(self):
        self.assertIn("Board layer", self.names(access.readable_layers(self.owner)))
        self.assertTrue(access.may_read(self.owner, self.board_layer))
        self.assertNotIn("Nobodys layer", self.names(access.readable_layers(self.owner)))

    def test_nobody_owns_a_layer_that_has_no_owner(self):
        """A reader with no Person must not match `owner IS NULL`: a kept
        layer with no owner stays hidden from them."""
        self.assertNotIn("Nobodys layer", self.names(access.readable_layers(self.stranger)))
        self.assertFalse(access.may_read(self.stranger, self.nobodys_layer))
        self.assertFalse(access.may_manage_clearances(self.stranger, self.nobodys_layer))

    def test_may_read_agrees_with_the_listings_for_every_reader(self):
        for user in self.readers:
            routes = set(access.readable_routes(user).values_list("pk", flat=True))
            layers = set(access.readable_layers(user).values_list("pk", flat=True))
            for route in self.routes:
                with self.subTest(user=user.username, route=route.name):
                    self.assertEqual(access.may_read(user, route), route.pk in routes)
            for layer in self.layers:
                with self.subTest(user=user.username, layer=layer.name):
                    self.assertEqual(access.may_read(user, layer), layer.pk in layers)

    def test_anything_but_a_route_or_a_layer_is_open(self):
        address = Address.objects.create(locality_name="Gdańsk")
        territory = Territory.objects.create(
            name="North", geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        for obj in (address, territory):
            self.assertTrue(access.may_read(self.stranger, obj))
            self.assertTrue(access.may_read(AnonymousUser(), obj))


class ManagingRuleTests(ClearanceFixture):
    def test_the_creator_and_superusers_choose_a_routes_clearances(self):
        self.assertTrue(access.may_manage_clearances(self.creator, self.kept))
        self.assertTrue(access.may_manage_clearances(self.creator, self.open))
        self.assertTrue(access.may_manage_clearances(self.root, self.kept))

    def test_staff_and_clearance_members_do_not(self):
        for user in (self.staff, self.member, self.both, self.stranger):
            with self.subTest(user=user.username):
                self.assertFalse(access.may_manage_clearances(user, self.kept))

    def test_a_route_nobody_created_is_the_superusers_alone(self):
        self.assertFalse(access.may_manage_clearances(self.creator, self.orphan))
        self.assertFalse(access.may_manage_clearances(self.member, self.orphan))
        self.assertTrue(access.may_manage_clearances(self.root, self.orphan))

    def test_the_layer_owner_chooses_the_layers_clearances(self):
        self.assertTrue(access.may_manage_clearances(self.owner, self.board_layer))
        self.assertFalse(access.may_manage_clearances(self.creator, self.board_layer))
        self.assertFalse(access.may_manage_clearances(self.member, self.board_layer))

    def test_an_anonymous_visitor_chooses_nothing(self):
        self.assertFalse(access.may_manage_clearances(AnonymousUser(), self.open))
        self.assertFalse(access.may_manage_clearances(AnonymousUser(), self.open_layer))

    def test_other_kinds_have_no_clearances_for_a_member_to_choose(self):
        address = Address.objects.create(locality_name="Gdańsk", created_by=self.creator)
        self.assertFalse(access.may_manage_clearances(self.creator, address))

    def test_an_anonymous_visitor_writes_nothing(self):
        self.assertFalse(access.may_write(AnonymousUser(), self.open))
        self.assertFalse(access.is_staff(AnonymousUser()))


class ClearancesSaveTests(ClearanceFixture):
    def url(self, obj, kind="route"):
        return reverse("locations:clearances_save", args=[kind, obj.pk])

    def post(self, user, obj, clearances, kind="route", **extra):
        self.client.force_login(user)
        return self.client.post(self.url(obj, kind), {"clearance": clearances, **extra})

    def clearance_names(self, obj):
        return sorted(obj.clearance_rows.values_list("clearance__name", flat=True))

    def said(self, response):
        """What this response told the member (the last message queued)."""
        return [str(m) for m in get_messages(response.wsgi_request)][-1:]

    def test_only_routes_and_layers_have_clearances(self):
        address = Address.objects.create(locality_name="Gdańsk")
        self.client.force_login(self.root)
        for kind, pk in (("address", address.pk), ("routechain", 1), ("zone", 1),
                         ("territory", 1), ("nonsense", self.open.pk)):
            with self.subTest(kind=kind):
                response = self.client.post(reverse("locations:clearances_save", args=[kind, pk]),
                                            {"clearance": [self.board.pk]})
                self.assertEqual(response.status_code, 404)

    def test_a_hidden_route_answers_404_not_403(self):
        for user in (self.stranger, self.staff):
            with self.subTest(user=user.username):
                self.assertEqual(self.post(user, self.kept, []).status_code, 404)
        self.assertEqual(self.clearance_names(self.kept), ["internal"])

    def test_a_reader_who_does_not_manage_is_refused(self):
        self.assertEqual(self.post(self.member, self.kept, []).status_code, 403)
        self.assertEqual(self.post(self.staff, self.open, [self.board.pk]).status_code, 403)
        self.assertEqual(self.clearance_names(self.kept), ["internal"])
        self.assertEqual(self.clearance_names(self.open), [])

    def test_saving_clearances_is_post_only(self):
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url(self.open)).status_code, 405)

    def test_an_anonymous_visitor_changes_nothing(self):
        response = self.client.post(self.url(self.open), {"clearance": [self.board.pk]})
        self.assertIn(response.status_code, (302, 401))
        self.assertEqual(self.clearance_names(self.open), [])

    def test_a_creator_cannot_keep_a_route_to_a_clearance_they_are_not_in(self):
        Person.objects.create(user=self.creator, display_name="C").clearances.add(self.board)
        response = self.post(self.creator, self.open, [self.seniors.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.clearance_names(self.open), [])
        self.assertEqual(self.said(response), ["Nothing changed."])

    def test_junk_values_are_ignored_not_a_crash(self):
        response = self.post(self.root, self.open,
                             ["abc", "-1", "１", "9" * 30, "", f"{self.board.pk}.0"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.clearance_names(self.open), [])

    def test_a_superuser_keeps_a_route_to_any_clearance(self):
        response = self.post(self.root, self.open, [self.seniors.pk, self.board.pk])
        self.assertEqual(self.clearance_names(self.open), ["confidential", "internal"])
        self.assertEqual(self.said(response), ["Saved."])
        self.assertFalse(access.may_read(self.stranger, self.open))

    def test_saving_the_same_clearances_again_changes_nothing_and_is_not_audited(self):
        Person.objects.create(user=self.creator, display_name="C").clearances.add(self.board)
        self.post(self.creator, self.open, [self.board.pk])
        response = self.post(self.creator, self.open, [self.board.pk])
        self.assertEqual(self.said(response), ["Nothing changed."])
        self.assertEqual(AuditRecord.objects.filter(
            action="LOCATIONS.ROUTE.CLEARANCES_CHANGED").count(), 1)

    def test_the_creator_may_keep_a_clearance_they_are_not_in_that_the_route_already_has(self):
        """The route's own clearances are always offered to its manager, so a
        save of the page does not silently drop them."""
        Person.objects.create(user=self.creator, display_name="C").clearances.add(self.board)
        self.post(self.creator, self.double, [self.board.pk, self.seniors.pk])
        self.assertEqual(self.clearance_names(self.double), ["confidential", "internal"])
        self.post(self.creator, self.double, [self.board.pk])
        self.assertEqual(self.clearance_names(self.double), ["internal"])
        self.post(self.creator, self.double, [self.board.pk, self.seniors.pk])
        self.assertEqual(self.clearance_names(self.double), ["internal"])     # gone from reach

    def test_clearing_every_clearance_opens_the_route_and_the_audit_says_so(self):
        response = self.post(self.creator, self.kept, [])
        self.assertEqual(response.status_code, 302)
        self.assertTrue(access.may_read(self.stranger, self.kept))
        record = AuditRecord.objects.get(action="LOCATIONS.ROUTE.CLEARANCES_CHANGED")
        self.assertEqual(record.metadata["before"], ["internal"])
        self.assertEqual(record.metadata["after"], [])
        self.assertTrue(record.metadata["open"])
        self.assertEqual(record.metadata["kind"], "route")
        self.assertEqual(record.actor_user_id, self.creator.pk)

    def test_the_form_returns_to_next_or_to_the_detail_page(self):
        response = self.post(self.creator, self.open, [])
        self.assertEqual(response["Location"],
                         reverse("locations:location_detail", args=["route", self.open.pk]))
        response = self.post(self.creator, self.open, [], next="/locations/")
        self.assertEqual(response["Location"], "/locations/")

    def test_the_layer_owner_keeps_a_layer_to_a_clearance_and_it_is_audited(self):
        layer = MapLayer.objects.create(name="Owned", slug="owned", owner=self.owner_person)
        self.owner_person.clearances.add(self.seniors)
        self.post(self.owner, layer, [self.seniors.pk], kind="maplayer")
        self.assertEqual(self.clearance_names(layer), ["confidential"])
        self.assertFalse(access.may_read(self.member, layer))
        self.assertTrue(access.may_read(fresh(self.both), layer))
        self.assertTrue(access.may_read(fresh(self.owner), layer))
        record = AuditRecord.objects.get(action="LOCATIONS.MAPLAYER.CLEARANCES_CHANGED")
        self.assertEqual((record.metadata["after"], record.metadata["open"]), (["confidential"], False))

    def test_a_member_may_not_choose_a_layers_clearances(self):
        self.assertEqual(self.post(self.member, self.board_layer, [], kind="maplayer").status_code, 403)
        self.assertEqual(self.post(self.stranger, self.board_layer, [], kind="maplayer").status_code, 404)
        self.assertEqual(self.clearance_names(self.board_layer), ["internal"])


class DetailSectionTests(ClearanceFixture):
    def page(self, user, kind, obj):
        self.client.force_login(user)
        return self.client.get(reverse("locations:location_detail", args=[kind, obj.pk]))

    def test_an_address_has_no_clearances_section(self):
        address = Address.objects.create(locality_name="Gdańsk")
        response = self.page(self.member, "address", address)
        self.assertEqual(response.context["clearances_kind"], "")
        self.assertEqual(response.context["clearances"], [])
        self.assertNotContains(response, 'data-testid="location-clearances"')

    def test_a_reader_sees_only_the_clearances_they_are_in(self):
        response = self.page(self.member, "route", self.double)
        self.assertEqual([c.name for c in response.context["clearances"]], ["internal"])
        self.assertTrue(response.context["clearances_restricted"])
        self.assertFalse(response.context["can_manage_clearances"])
        self.assertEqual(response.context["clearance_choices"], [])
        self.assertEqual(response.context["clearances_save_url"], "")
        self.assertNotContains(response, "confidential")

    def test_the_manager_sees_every_clearance_and_which_are_ticked(self):
        Person.objects.create(user=self.creator, display_name="C").clearances.add(self.board)
        response = self.page(self.creator, "route", self.kept)
        self.assertTrue(response.context["can_manage_clearances"])
        self.assertEqual([c.name for c in response.context["clearances"]], ["internal"])
        choices = {row["clearance"].name: row["on"] for row in response.context["clearance_choices"]}
        self.assertEqual(choices, {"internal": True})           # not the confidential: not theirs
        self.assertContains(response, reverse("locations:clearances_save", args=["route", self.kept.pk]))

    def test_the_manager_is_offered_the_routes_own_clearances_they_are_not_in(self):
        response = self.page(self.creator, "route", self.double)
        self.assertEqual({row["clearance"].name for row in response.context["clearance_choices"]},
                         {"internal", "confidential"})
        self.assertEqual([c.name for c in response.context["clearances"]], ["confidential", "internal"])

    def test_a_superuser_is_offered_every_clearance(self):
        response = self.page(self.root, "route", self.open)
        choices = {row["clearance"].name: row["on"] for row in response.context["clearance_choices"]}
        self.assertEqual(choices, {"internal": False, "confidential": False})

    def test_an_open_route_says_every_member_sees_it(self):
        response = self.page(self.stranger, "route", self.open)
        self.assertFalse(response.context["clearances_restricted"])
        self.assertContains(response, "Every member signed in.")

    def test_a_manager_in_no_clearance_is_told_there_is_none_to_choose(self):
        response = self.page(self.creator, "route", self.open)
        self.assertTrue(response.context["can_manage_clearances"])
        self.assertContains(response, "You are in no clearance, so there is none you can keep this to.")

    def test_the_layer_page_shows_its_clearances_and_no_metadata_editor(self):
        response = self.page(self.member, "maplayer", self.board_layer)
        self.assertEqual(response.context["clearances_kind"], "maplayer")
        self.assertEqual([c.name for c in response.context["clearances"]], ["internal"])
        self.assertFalse(response.context["has_metadata"])
        self.assertIn(("Polygons", 1), response.context["fields"])
        self.assertIn(("Owner", "Owner"), response.context["fields"])

    def test_the_owner_sees_the_layer_section_with_its_save_door(self):
        response = self.page(self.owner, "maplayer", self.board_layer)
        self.assertTrue(response.context["can_manage_clearances"])
        self.assertEqual(response.context["clearances_save_url"],
                         reverse("locations:clearances_save", args=["maplayer", self.board_layer.pk]))

    def test_a_kept_route_is_missing_for_staff_on_every_page(self):
        self.client.force_login(self.staff)
        for url in (reverse("locations:location_detail", args=["route", self.kept.pk]),
                    reverse("locations:route_detail", args=[self.kept.pk]),
                    reverse("locations:route_review", args=[self.kept.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_hidden_routes_note_and_metadata_are_missing_too(self):
        self.client.force_login(self.staff)
        note = self.client.post(reverse("locations:note_save", args=["route", self.kept.pk]),
                                {"note": "staff was here"})
        metadata = self.client.post(reverse("locations:metadata_save", args=["route", self.kept.pk]),
                                    {"metadata": "{}", "format": "json"})
        self.assertEqual((note.status_code, metadata.status_code), (404, 404))
        self.kept.refresh_from_db()
        self.assertEqual(self.kept.notes, "")


class MapPageTests(ClearanceFixture):
    def payload(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("locations:locations_all"))
        self.assertEqual(response.status_code, 200)
        return (json.loads(response.context["locations_json"]),
                json.loads(response.context["map_layers_json"]))

    def test_an_inactive_layer_is_on_nobodys_map(self):
        _locations, layers = self.payload(self.root)
        self.assertEqual({layer["name"] for layer in layers},
                         {"Open layer", "Board layer", "Nobodys layer"})

    def test_each_reader_sees_their_layers(self):
        cases = {
            self.stranger: {"Open layer"},
            self.owner: {"Open layer", "Board layer"},
            self.member: {"Open layer", "Board layer", "Nobodys layer"},
            self.staff: {"Open layer"},
        }
        for user, expected in cases.items():
            with self.subTest(user=user.username):
                self.assertEqual({layer["name"] for layer in self.payload(user)[1]}, expected)

    def test_a_layers_polygons_travel_with_it(self):
        layers = {layer["slug"]: layer for layer in self.payload(self.member)[1]}
        polygons = layers["board-layer"]["polygons"]
        self.assertEqual([p["name"] for p in polygons], ["Board layer cell"])
        self.assertEqual(polygons[0]["geometry"]["type"], "Polygon")
        self.assertEqual(polygons[0]["center"]["type"], "Point")

    def test_a_route_kept_to_two_clearances_is_drawn_once(self):
        locations, _layers = self.payload(self.both)
        self.assertEqual([row["name"] for row in locations if row["type"] == "Route"].count(
            "DoubleRoute"), 1)

    def test_a_routes_row_links_its_own_detail_page(self):
        locations, _layers = self.payload(self.stranger)
        row = next(row for row in locations if row["name"] == "OpenRoute")
        self.assertEqual(row["detail_url"],
                         reverse("locations:location_detail", args=["route", self.open.pk]))
        self.assertEqual(row["geometry"]["type"], "MultiLineString")

    def test_the_viewers_items_from_the_map_providers_are_merged_in(self):
        from unittest import mock

        from toto.locations.plugins.map_plugins import LocationMapPlugin

        def mine(request):
            return [{"type": "Pin", "name": f"for {request.user.username}", "geometry": None}]

        with mock.patch.object(LocationMapPlugin, "_providers", [mine]):
            locations, _layers = self.payload(self.member)
        self.assertIn("for member", [row["name"] for row in locations])


@skipUnless(HAS_GIS, "the map API reads geometry")
class JsonDoorTests(ClearanceFixture):
    def get(self, user, name):
        self.client.force_login(add_to_mesh(user))
        response = self.client.get(reverse(f"locations:{name}"))
        self.assertEqual(response.status_code, 200)
        return response.json()

    def route_names(self, user):
        return {row["name"] for row in self.get(user, "api_map_data")["locations"]
                if row["type"] == "Route"}

    def test_the_map_api_leaves_out_the_routes_a_caller_may_not_read(self):
        self.assertEqual(self.route_names(self.stranger), {"OpenRoute"})
        self.assertEqual(self.route_names(self.member),
                         {"OpenRoute", "BoardRoute", "DoubleRoute", "OrphanRoute"})
        self.assertEqual(self.route_names(self.creator), {"OpenRoute", "BoardRoute", "DoubleRoute"})

    def test_the_layers_api_leaves_out_kept_and_inactive_layers(self):
        names = {layer["name"] for layer in self.get(self.stranger, "api_map_layers")["layers"]}
        self.assertEqual(names, {"Open layer"})
        layers = {layer["slug"]: layer for layer in self.get(self.member, "api_map_layers")["layers"]}
        self.assertEqual(set(layers), {"open-layer", "board-layer", "nobodys-layer"})
        self.assertEqual(layers["board-layer"]["polygons"][0]["name"], "Board layer cell")

    def test_a_member_outside_the_mesh_is_gated(self):
        self.client.force_login(self.member)
        for name in ("api_map_data", "api_map_layers"):
            with self.subTest(name=name):
                response = self.client.get(reverse(f"locations:{name}"))
                self.assertEqual(response.status_code, 403)
                self.assertTrue(response.json()["gated"])


class ClearanceTargetKindTests(ClearanceFixture):
    """``locations.route`` and ``locations.map_layer`` — the kinds the
    socialhub's New clearance modal offers for routes and layers (2026-09-30,
    "what a clearance clears"). Search by name, resolve, keep — with the
    fixture's real rows: OpenRoute, BoardRoute (internal), DoubleRoute (both),
    OrphanRoute (internal); Open, Board (internal), Nobodys (internal) and
    Idle layers."""

    ROUTE_ACTION = "LOCATIONS.ROUTE.CLEARANCES_CHANGED"
    LAYER_ACTION = "LOCATIONS.MAPLAYER.CLEARANCES_CHANGED"

    def setUp(self):
        from toto.socialhub.plugins.clearance_plugins import kind

        self.routes_kind = kind("locations.route")
        self.layers_kind = kind("locations.map_layer")

    @staticmethod
    def line():
        from django.contrib.gis.geos import LineString, MultiLineString

        return MultiLineString(LineString((18.6, 54.3), (21.0, 52.2)))

    @staticmethod
    def labels(rows):
        return [row["label"] for row in rows]

    @staticmethod
    def kept_to(obj):
        from toto.socialhub import clearance_access

        return [c.name for c in clearance_access.clearances_of(obj, rows="clearance_rows")]

    # -- registration ----------------------------------------------------------

    def test_both_are_registered_under_their_keys_with_a_title_and_an_icon(self):
        from django.utils import translation

        from toto.socialhub.plugins.clearance_plugins import kinds

        keys = [k.get_key() for k in kinds()]
        self.assertIn("locations.route", keys)
        self.assertIn("locations.map_layer", keys)
        self.assertLess(keys.index("locations.route"), keys.index("locations.map_layer"))
        self.assertEqual((self.routes_kind.icon, self.layers_kind.icon), ("route", "layer-group"))
        with translation.override("en"):
            self.assertEqual(str(self.routes_kind.get_title()), "Routes")
            self.assertEqual(str(self.layers_kind.get_title()), "Map layers")

    def test_both_titles_are_polish_in_polish(self):
        from django.utils import translation

        with translation.override("pl"):
            self.assertEqual(str(self.routes_kind.get_title()), "Trasy")
            self.assertEqual(str(self.layers_kind.get_title()), "Warstwy mapy")

    # -- search ----------------------------------------------------------------

    def test_route_search_matches_the_name_case_insensitively(self):
        self.assertEqual(self.labels(self.routes_kind.search("boardROUTE")), ["BoardRoute"])
        self.assertEqual(self.routes_kind.search("no such route"), [])

    def test_route_search_is_ordered_by_name_and_capped_by_limit(self):
        self.assertEqual(self.labels(self.routes_kind.search("route")),
                         ["BoardRoute", "DoubleRoute", "OpenRoute", "OrphanRoute"])
        self.assertEqual(self.labels(self.routes_kind.search("route", limit=2)),
                         ["BoardRoute", "DoubleRoute"])

    def test_route_search_offers_kept_routes_too_for_the_superuser(self):
        # Nothing is filtered by reader: the modal is a superuser's.
        self.assertIn("OrphanRoute", self.labels(self.routes_kind.search("orphan")))

    def test_a_route_row_is_pk_label_and_an_empty_detail(self):
        self.assertEqual(self.routes_kind.search("OpenRoute"),
                         [{"pk": self.open.pk, "label": "OpenRoute", "detail": ""}])

    def test_a_route_without_a_name_is_labelled_by_its_pk(self):
        from django.utils import translation

        nameless = Route.objects.create(name="", geometry=self.line())
        with translation.override("en"):
            rows = self.routes_kind.search("")
        self.assertIn({"pk": nameless.pk, "label": f"Route {nameless.pk}", "detail": ""}, rows)
        self.assertEqual(rows[0]["pk"], nameless.pk)            # "" sorts first

    def test_layer_search_matches_the_name_case_insensitively(self):
        self.assertEqual(self.labels(self.layers_kind.search("BOARD")), ["Board layer"])

    def test_layer_search_is_ordered_by_name_capped_and_offers_inactive_layers(self):
        self.assertEqual(self.labels(self.layers_kind.search("Layer")),
                         ["Board layer", "Idle layer", "Nobodys layer", "Open layer"])
        self.assertEqual(self.labels(self.layers_kind.search("layer", limit=1)), ["Board layer"])

    def test_a_layer_row_is_pk_name_and_slug(self):
        self.assertEqual(self.layers_kind.search("open layer"),
                         [{"pk": self.open_layer.pk, "label": "Open layer", "detail": "open-layer"}])

    # -- resolve ---------------------------------------------------------------

    def test_resolve_ignores_unknown_pks_and_is_ordered_by_name(self):
        self.assertEqual(self.routes_kind.resolve([self.orphan.pk, 987654, self.kept.pk]),
                         [self.kept, self.orphan])
        self.assertEqual(self.layers_kind.resolve([self.open_layer.pk, 987654, self.idle_layer.pk]),
                         [self.idle_layer, self.open_layer])
        self.assertEqual(self.routes_kind.resolve([987654]), [])

    # -- keep: routes ------------------------------------------------------------

    def test_keep_adds_the_clearance_to_a_route_already_kept_to_another(self):
        self.assertEqual(self.routes_kind.keep([self.kept], self.seniors, actor=self.root), 1)
        self.assertEqual(self.kept_to(self.kept), ["confidential", "internal"])
        self.assertIn("BoardRoute", self.names(access.readable_routes(self.member)))

    def test_keep_closes_an_open_route_to_readers_outside_the_clearance(self):
        self.assertIn("OpenRoute", self.names(access.readable_routes(self.stranger)))
        self.routes_kind.keep([self.open], self.board, actor=self.root)
        self.assertNotIn("OpenRoute", self.names(access.readable_routes(self.stranger)))
        self.assertNotIn("OpenRoute", self.names(access.readable_routes(self.staff)))
        self.assertNotIn("OpenRoute", self.names(access.readable_routes(AnonymousUser())))
        self.assertFalse(access.may_read(self.stranger, self.open))
        self.assertIn("OpenRoute", self.names(access.readable_routes(self.member)))
        self.assertIn("OpenRoute", self.names(access.readable_routes(self.creator)))

    def test_keeping_a_route_writes_the_locations_audit_record_with_the_actor(self):
        self.routes_kind.keep([self.open], self.board, actor=self.root)
        record = AuditRecord.objects.get(action=self.ROUTE_ACTION)
        self.assertEqual(record.actor_user_id, self.root.pk)
        self.assertEqual(record.metadata["kind"], "route")
        self.assertEqual((record.metadata["before"], record.metadata["after"]), ([], ["internal"]))
        self.assertFalse(record.metadata["open"])

    def test_keeping_a_route_twice_is_a_no_op(self):
        self.routes_kind.keep([self.open], self.board, actor=self.root)
        self.routes_kind.keep([self.open], self.board, actor=self.root)
        self.assertEqual(RouteClearance.objects.filter(route=self.open).count(), 1)
        self.assertEqual(AuditRecord.objects.filter(action=self.ROUTE_ACTION).count(), 1)

    def test_keeping_a_route_to_a_clearance_it_has_records_nothing(self):
        self.routes_kind.keep([self.double], self.seniors, actor=self.root)
        self.assertEqual(self.kept_to(self.double), ["confidential", "internal"])
        self.assertFalse(AuditRecord.objects.filter(action=self.ROUTE_ACTION).exists())

    def test_keep_takes_several_routes(self):
        self.assertEqual(self.routes_kind.keep([self.open, self.orphan], self.seniors,
                                               actor=self.root), 2)
        self.assertEqual(self.kept_to(self.open), ["confidential"])
        self.assertEqual(self.kept_to(self.orphan), ["confidential", "internal"])
        self.assertEqual(AuditRecord.objects.filter(action=self.ROUTE_ACTION).count(), 2)

    # -- keep: layers ------------------------------------------------------------

    def test_keep_adds_the_clearance_to_a_layer_already_kept_to_another(self):
        self.layers_kind.keep([self.board_layer], self.seniors, actor=self.root)
        self.assertEqual(self.kept_to(self.board_layer), ["confidential", "internal"])
        self.assertIn("Board layer", self.names(access.readable_layers(self.member)))

    def test_keep_closes_an_open_layer_to_readers_outside_the_clearance(self):
        self.assertIn("Open layer", self.names(access.readable_layers(self.stranger)))
        self.layers_kind.keep([self.open_layer], self.board, actor=self.root)
        self.assertNotIn("Open layer", self.names(access.readable_layers(self.stranger)))
        self.assertNotIn("Open layer", self.names(access.readable_layers(self.staff)))
        self.assertFalse(access.may_read(self.stranger, self.open_layer))
        self.assertIn("Open layer", self.names(access.readable_layers(self.member)))

    def test_keeping_a_layer_writes_the_locations_audit_record_with_the_actor(self):
        self.layers_kind.keep([self.open_layer], self.board, actor=self.root)
        record = AuditRecord.objects.get(action=self.LAYER_ACTION)
        self.assertEqual(record.actor_user_id, self.root.pk)
        self.assertEqual(record.metadata["kind"], "maplayer")
        self.assertEqual(record.metadata["after"], ["internal"])
        self.assertFalse(AuditRecord.objects.filter(action=self.ROUTE_ACTION).exists())

    def test_keeping_a_layer_twice_is_a_no_op(self):
        self.layers_kind.keep([self.open_layer], self.board, actor=self.root)
        self.layers_kind.keep([self.open_layer], self.board, actor=self.root)
        self.assertEqual(MapLayerClearance.objects.filter(layer=self.open_layer).count(), 1)
        self.assertEqual(AuditRecord.objects.filter(action=self.LAYER_ACTION).count(), 1)
