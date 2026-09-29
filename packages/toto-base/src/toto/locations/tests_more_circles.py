"""Routes and map layers kept to circles (2026-09-29), rule by rule.

`tests_circles` pins the headline: a hidden route is a missing one. These
walk the rule's corners — who reads (members, the creator or owner,
superusers; not staff, not a functional community), who chooses the circles,
what `circles_save` refuses, what the detail page's section shows to whom,
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
    HAS_GIS, Address, MapLayer, MapLayerCircle, Route, RouteCircle, Territory,
)
from toto.people.models import Person
from toto.socialhub.models import Community

User = get_user_model()


def fresh(user):
    """The user re-read, so a Person made in the test is seen (the reverse
    one-to-one caches its absence on the instance)."""
    return User.objects.get(pk=user.pk)


@skipUnless(HAS_GIS, "routes and layers draw on geometry; the map 404s without GIS")
class CircleFixture(TestCase):
    """Two circles and a functional community; readers of every kind; four
    routes (open, kept, kept to both circles, kept with no creator) and four
    layers (open, kept with an owner, kept with none, inactive)."""

    @classmethod
    def setUpTestData(cls):
        from django.contrib.gis.geos import LineString, MultiLineString, Polygon

        from toto.locations.models import MapLayerPolygon

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.board = Community.objects.create(name="board", slug="board", is_circle=True)
        cls.seniors = Community.objects.create(name="seniors", slug="seniors", is_circle=True)
        cls.guild = Community.objects.create(name="guild", slug="guild")          # functional

        cls.creator = User.objects.create_user("creator", password="x")
        cls.member = User.objects.create_user("member", password="x")
        Person.objects.create(user=cls.member, display_name="Member").communities.add(cls.board)
        cls.both = User.objects.create_user("both", password="x")
        Person.objects.create(user=cls.both, display_name="Both").communities.add(
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
        RouteCircle.objects.create(route=cls.kept, circle=cls.board)
        cls.double = Route.objects.create(name="DoubleRoute", created_by=cls.creator,
                                          geometry=line((20.0, 51.0), (20.5, 51.5)))
        RouteCircle.objects.create(route=cls.double, circle=cls.board)
        RouteCircle.objects.create(route=cls.double, circle=cls.seniors)
        cls.orphan = Route.objects.create(name="OrphanRoute",
                                          geometry=line((21.0, 52.0), (21.5, 52.5)))
        RouteCircle.objects.create(route=cls.orphan, circle=cls.board)

        square = Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), srid=4326)
        cls.open_layer = MapLayer.objects.create(name="Open layer", slug="open-layer")
        cls.board_layer = MapLayer.objects.create(name="Board layer", slug="board-layer",
                                                  owner=cls.owner_person)
        MapLayerCircle.objects.create(layer=cls.board_layer, circle=cls.board)
        cls.nobodys_layer = MapLayer.objects.create(name="Nobodys layer", slug="nobodys-layer")
        MapLayerCircle.objects.create(layer=cls.nobodys_layer, circle=cls.board)
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


class ReadingRuleTests(CircleFixture):
    def test_a_stranger_reads_only_the_open_routes(self):
        self.assertEqual(self.names(access.readable_routes(self.stranger)), {"OpenRoute"})

    def test_a_circle_member_reads_the_routes_kept_to_their_circle(self):
        self.assertEqual(self.names(access.readable_routes(self.member)),
                         {"OpenRoute", "BoardRoute", "DoubleRoute", "OrphanRoute"})

    def test_a_route_kept_to_two_circles_is_listed_once_for_a_member_of_both(self):
        rows = list(access.readable_routes(self.both).values_list("name", flat=True))
        self.assertEqual(rows.count("DoubleRoute"), 1)

    def test_the_creator_reads_their_kept_routes_without_being_in_the_circle(self):
        self.assertEqual(self.names(access.readable_routes(self.creator)),
                         {"OpenRoute", "BoardRoute", "DoubleRoute"})

    def test_staff_is_not_a_circle(self):
        self.assertEqual(self.names(access.readable_routes(self.staff)), {"OpenRoute"})
        self.assertFalse(access.may_read(self.staff, self.kept))

    def test_a_functional_community_never_grants_reading(self):
        """A row naming a functional community (the admin's limit_choices_to
        is only a form hint) keeps the route kept, and grants its members
        nothing."""
        route = Route.objects.create(name="GuildRoute", geometry=self.open.geometry)
        RouteCircle.objects.create(route=route, circle=self.guild)
        self.assertNotIn("GuildRoute", self.names(access.readable_routes(self.guildsman)))
        self.assertFalse(access.may_read(self.guildsman, route))
        self.assertFalse(access.may_read(self.stranger, route))

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
        self.assertFalse(access.may_manage_circles(self.stranger, self.nobodys_layer))

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


class ManagingRuleTests(CircleFixture):
    def test_the_creator_and_superusers_choose_a_routes_circles(self):
        self.assertTrue(access.may_manage_circles(self.creator, self.kept))
        self.assertTrue(access.may_manage_circles(self.creator, self.open))
        self.assertTrue(access.may_manage_circles(self.root, self.kept))

    def test_staff_and_circle_members_do_not(self):
        for user in (self.staff, self.member, self.both, self.stranger):
            with self.subTest(user=user.username):
                self.assertFalse(access.may_manage_circles(user, self.kept))

    def test_a_route_nobody_created_is_the_superusers_alone(self):
        self.assertFalse(access.may_manage_circles(self.creator, self.orphan))
        self.assertFalse(access.may_manage_circles(self.member, self.orphan))
        self.assertTrue(access.may_manage_circles(self.root, self.orphan))

    def test_the_layer_owner_chooses_the_layers_circles(self):
        self.assertTrue(access.may_manage_circles(self.owner, self.board_layer))
        self.assertFalse(access.may_manage_circles(self.creator, self.board_layer))
        self.assertFalse(access.may_manage_circles(self.member, self.board_layer))

    def test_an_anonymous_visitor_chooses_nothing(self):
        self.assertFalse(access.may_manage_circles(AnonymousUser(), self.open))
        self.assertFalse(access.may_manage_circles(AnonymousUser(), self.open_layer))

    def test_other_kinds_have_no_circles_for_a_member_to_choose(self):
        address = Address.objects.create(locality_name="Gdańsk", created_by=self.creator)
        self.assertFalse(access.may_manage_circles(self.creator, address))

    def test_an_anonymous_visitor_writes_nothing(self):
        self.assertFalse(access.may_write(AnonymousUser(), self.open))
        self.assertFalse(access.is_staff(AnonymousUser()))


class CirclesSaveTests(CircleFixture):
    def url(self, obj, kind="route"):
        return reverse("locations:circles_save", args=[kind, obj.pk])

    def post(self, user, obj, circles, kind="route", **extra):
        self.client.force_login(user)
        return self.client.post(self.url(obj, kind), {"circle": circles, **extra})

    def circle_names(self, obj):
        return sorted(obj.circle_rows.values_list("circle__name", flat=True))

    def said(self, response):
        """What this response told the member (the last message queued)."""
        return [str(m) for m in get_messages(response.wsgi_request)][-1:]

    def test_only_routes_and_layers_have_circles(self):
        address = Address.objects.create(locality_name="Gdańsk")
        self.client.force_login(self.root)
        for kind, pk in (("address", address.pk), ("routechain", 1), ("zone", 1),
                         ("territory", 1), ("nonsense", self.open.pk)):
            with self.subTest(kind=kind):
                response = self.client.post(reverse("locations:circles_save", args=[kind, pk]),
                                            {"circle": [self.board.pk]})
                self.assertEqual(response.status_code, 404)

    def test_a_hidden_route_answers_404_not_403(self):
        for user in (self.stranger, self.staff):
            with self.subTest(user=user.username):
                self.assertEqual(self.post(user, self.kept, []).status_code, 404)
        self.assertEqual(self.circle_names(self.kept), ["board"])

    def test_a_reader_who_does_not_manage_is_refused(self):
        self.assertEqual(self.post(self.member, self.kept, []).status_code, 403)
        self.assertEqual(self.post(self.staff, self.open, [self.board.pk]).status_code, 403)
        self.assertEqual(self.circle_names(self.kept), ["board"])
        self.assertEqual(self.circle_names(self.open), [])

    def test_saving_circles_is_post_only(self):
        self.client.force_login(self.creator)
        self.assertEqual(self.client.get(self.url(self.open)).status_code, 405)

    def test_an_anonymous_visitor_changes_nothing(self):
        response = self.client.post(self.url(self.open), {"circle": [self.board.pk]})
        self.assertIn(response.status_code, (302, 401))
        self.assertEqual(self.circle_names(self.open), [])

    def test_a_creator_cannot_keep_a_route_to_a_circle_they_are_not_in(self):
        Person.objects.create(user=self.creator, display_name="C").communities.add(self.board)
        response = self.post(self.creator, self.open, [self.seniors.pk])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.circle_names(self.open), [])
        self.assertEqual(self.said(response), ["Nothing changed."])

    def test_a_functional_community_is_never_given_a_route(self):
        Person.objects.create(user=self.creator, display_name="C").communities.add(self.guild)
        self.post(self.creator, self.open, [self.guild.pk])
        self.assertEqual(self.circle_names(self.open), [])
        self.post(self.root, self.open, [self.guild.pk])
        self.assertEqual(self.circle_names(self.open), [])

    def test_junk_values_are_ignored_not_a_crash(self):
        response = self.post(self.root, self.open,
                             ["abc", "-1", "１", "9" * 30, "", f"{self.board.pk}.0"])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.circle_names(self.open), [])

    def test_a_superuser_keeps_a_route_to_any_circle(self):
        response = self.post(self.root, self.open, [self.seniors.pk, self.board.pk])
        self.assertEqual(self.circle_names(self.open), ["board", "seniors"])
        self.assertEqual(self.said(response), ["Saved."])
        self.assertFalse(access.may_read(self.stranger, self.open))

    def test_saving_the_same_circles_again_changes_nothing_and_is_not_audited(self):
        Person.objects.create(user=self.creator, display_name="C").communities.add(self.board)
        self.post(self.creator, self.open, [self.board.pk])
        response = self.post(self.creator, self.open, [self.board.pk])
        self.assertEqual(self.said(response), ["Nothing changed."])
        self.assertEqual(AuditRecord.objects.filter(
            action="LOCATIONS.ROUTE.CIRCLES_CHANGED").count(), 1)

    def test_the_creator_may_keep_a_circle_they_are_not_in_that_the_route_already_has(self):
        """The route's own circles are always offered to its manager, so a
        save of the page does not silently drop them."""
        Person.objects.create(user=self.creator, display_name="C").communities.add(self.board)
        self.post(self.creator, self.double, [self.board.pk, self.seniors.pk])
        self.assertEqual(self.circle_names(self.double), ["board", "seniors"])
        self.post(self.creator, self.double, [self.board.pk])
        self.assertEqual(self.circle_names(self.double), ["board"])
        self.post(self.creator, self.double, [self.board.pk, self.seniors.pk])
        self.assertEqual(self.circle_names(self.double), ["board"])     # gone from reach

    def test_clearing_every_circle_opens_the_route_and_the_audit_says_so(self):
        response = self.post(self.creator, self.kept, [])
        self.assertEqual(response.status_code, 302)
        self.assertTrue(access.may_read(self.stranger, self.kept))
        record = AuditRecord.objects.get(action="LOCATIONS.ROUTE.CIRCLES_CHANGED")
        self.assertEqual(record.metadata["before"], ["board"])
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

    def test_the_layer_owner_keeps_a_layer_to_a_circle_and_it_is_audited(self):
        layer = MapLayer.objects.create(name="Owned", slug="owned", owner=self.owner_person)
        self.owner_person.communities.add(self.seniors)
        self.post(self.owner, layer, [self.seniors.pk], kind="maplayer")
        self.assertEqual(self.circle_names(layer), ["seniors"])
        self.assertFalse(access.may_read(self.member, layer))
        self.assertTrue(access.may_read(fresh(self.both), layer))
        self.assertTrue(access.may_read(fresh(self.owner), layer))
        record = AuditRecord.objects.get(action="LOCATIONS.MAPLAYER.CIRCLES_CHANGED")
        self.assertEqual((record.metadata["after"], record.metadata["open"]), (["seniors"], False))

    def test_a_member_may_not_choose_a_layers_circles(self):
        self.assertEqual(self.post(self.member, self.board_layer, [], kind="maplayer").status_code, 403)
        self.assertEqual(self.post(self.stranger, self.board_layer, [], kind="maplayer").status_code, 404)
        self.assertEqual(self.circle_names(self.board_layer), ["board"])


class DetailSectionTests(CircleFixture):
    def page(self, user, kind, obj):
        self.client.force_login(user)
        return self.client.get(reverse("locations:location_detail", args=[kind, obj.pk]))

    def test_an_address_has_no_circles_section(self):
        address = Address.objects.create(locality_name="Gdańsk")
        response = self.page(self.member, "address", address)
        self.assertEqual(response.context["circles_kind"], "")
        self.assertEqual(response.context["circles"], [])
        self.assertNotContains(response, 'data-testid="location-circles"')

    def test_a_reader_sees_only_the_circles_they_are_in(self):
        response = self.page(self.member, "route", self.double)
        self.assertEqual([c.name for c in response.context["circles"]], ["board"])
        self.assertTrue(response.context["circles_restricted"])
        self.assertFalse(response.context["can_manage_circles"])
        self.assertEqual(response.context["circle_choices"], [])
        self.assertEqual(response.context["circles_save_url"], "")
        self.assertNotContains(response, "seniors")

    def test_the_manager_sees_every_circle_and_which_are_ticked(self):
        Person.objects.create(user=self.creator, display_name="C").communities.add(self.board)
        response = self.page(self.creator, "route", self.kept)
        self.assertTrue(response.context["can_manage_circles"])
        self.assertEqual([c.name for c in response.context["circles"]], ["board"])
        choices = {row["circle"].name: row["on"] for row in response.context["circle_choices"]}
        self.assertEqual(choices, {"board": True})           # not the seniors: not theirs
        self.assertContains(response, reverse("locations:circles_save", args=["route", self.kept.pk]))

    def test_the_manager_is_offered_the_routes_own_circles_they_are_not_in(self):
        response = self.page(self.creator, "route", self.double)
        self.assertEqual({row["circle"].name for row in response.context["circle_choices"]},
                         {"board", "seniors"})
        self.assertEqual([c.name for c in response.context["circles"]], ["board", "seniors"])

    def test_a_superuser_is_offered_every_circle(self):
        response = self.page(self.root, "route", self.open)
        choices = {row["circle"].name: row["on"] for row in response.context["circle_choices"]}
        self.assertEqual(choices, {"board": False, "seniors": False})

    def test_an_open_route_says_every_member_sees_it(self):
        response = self.page(self.stranger, "route", self.open)
        self.assertFalse(response.context["circles_restricted"])
        self.assertContains(response, "Every member signed in.")

    def test_a_manager_in_no_circle_is_told_there_is_none_to_choose(self):
        response = self.page(self.creator, "route", self.open)
        self.assertTrue(response.context["can_manage_circles"])
        self.assertContains(response, "You are in no circle, so there is none you can keep this to.")

    def test_the_layer_page_shows_its_circles_and_no_metadata_editor(self):
        response = self.page(self.member, "maplayer", self.board_layer)
        self.assertEqual(response.context["circles_kind"], "maplayer")
        self.assertEqual([c.name for c in response.context["circles"]], ["board"])
        self.assertFalse(response.context["has_metadata"])
        self.assertIn(("Polygons", 1), response.context["fields"])
        self.assertIn(("Owner", "Owner"), response.context["fields"])

    def test_the_owner_sees_the_layer_section_with_its_save_door(self):
        response = self.page(self.owner, "maplayer", self.board_layer)
        self.assertTrue(response.context["can_manage_circles"])
        self.assertEqual(response.context["circles_save_url"],
                         reverse("locations:circles_save", args=["maplayer", self.board_layer.pk]))

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


class MapPageTests(CircleFixture):
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

    def test_a_route_kept_to_two_circles_is_drawn_once(self):
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
class JsonDoorTests(CircleFixture):
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
