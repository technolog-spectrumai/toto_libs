"""Map domains keep map items to clearances (2026-09-30), rule by rule.

`tests_clearances` pins the headline: a hidden item is a missing one on every
locations door. These walk the rule's corners for all five kinds here
(routes, map layers, addresses, zones, territories): who reads (superusers,
and whoever holds a clearance of EVERY kept domain of the item — not its
creator or owner, not staff, not a community), what a domain with no
clearances does, what a domain with two clearances does, and that the map
page and the JSON doors agree with the per-object door. The Domains tab
itself is `tests_domains`.
"""

import json
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import NoReverseMatch, reverse

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import (
    HAS_GIS, Address, AddressInDomain, MapDomain, MapDomainClearance, MapLayer,
    MapLayerInDomain, Route, RouteChain, RouteInDomain, Territory, TerritoryInDomain, Zone,
    ZoneInDomain,
)
from toto.people.models import Person
from toto.socialhub.models import Clearance, Community

User = get_user_model()

SQUARE = "POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"
MULTI_SQUARE = "MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))"

#: The typed membership table of each kind, and its FK to the item.
THROUGH = {
    Route: (RouteInDomain, "route"),
    MapLayer: (MapLayerInDomain, "map_layer"),
    Address: (AddressInDomain, "address"),
    Zone: (ZoneInDomain, "zone"),
    Territory: (TerritoryInDomain, "territory"),
}


def fresh(user):
    """The user re-read, so a Person made in the test is seen (the reverse
    one-to-one caches its absence on the instance)."""
    return User.objects.get(pk=user.pk)


def keep(item, *domains):
    """Put ``item`` in ``domains``."""
    through, field = THROUGH[type(item)]
    for domain in domains:
        through.objects.create(domain=domain, **{field: item})
    return item


def domain(name, *clearances):
    made = MapDomain.objects.create(name=name)
    for clearance in clearances:
        MapDomainClearance.objects.create(domain=made, clearance=clearance)
    return made


@skipUnless(HAS_GIS, "routes and layers draw on geometry; the map 404s without GIS")
class ClearanceFixture(TestCase):
    """Two clearances and a plain community; readers of every kind; four
    domains (kept to ``internal``, kept to ``confidential``, kept to either,
    kept to nothing); four routes (open; in the internal domain; in the
    internal AND the confidential domain; in the internal domain with no
    creator) and four layers (open; in the internal domain with an owner;
    the same with none; inactive)."""

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
        cls.senior = User.objects.create_user("senior", password="x")
        Person.objects.create(user=cls.senior, display_name="Senior").clearances.add(cls.seniors)
        cls.guildsman = User.objects.create_user("guildsman", password="x")
        Person.objects.create(user=cls.guildsman, display_name="Guildsman").communities.add(cls.guild)
        cls.stranger = User.objects.create_user("stranger", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.root = User.objects.create_superuser("root", "r@example.org", "x")
        cls.owner = User.objects.create_user("owner", password="x")
        cls.owner_person = Person.objects.create(user=cls.owner, display_name="Owner")

        cls.board_domain = domain("Board", cls.board)
        cls.senior_domain = domain("Seniors", cls.seniors)
        cls.either_domain = domain("Either", cls.board, cls.seniors)
        cls.loose_domain = domain("Loose")                                  # keeps nothing

        def line(*points):
            return MultiLineString(LineString(*points))

        cls.open = Route.objects.create(name="OpenRoute", created_by=cls.creator,
                                        geometry=line((18.0, 54.0), (18.5, 54.5)))
        cls.kept = keep(Route.objects.create(name="BoardRoute", created_by=cls.creator,
                                             geometry=line((19.0, 50.0), (19.5, 50.5))),
                        cls.board_domain)
        cls.double = keep(Route.objects.create(name="DoubleRoute", created_by=cls.creator,
                                               geometry=line((20.0, 51.0), (20.5, 51.5))),
                          cls.board_domain, cls.senior_domain)
        cls.orphan = keep(Route.objects.create(name="OrphanRoute",
                                               geometry=line((21.0, 52.0), (21.5, 52.5))),
                          cls.board_domain)

        square = Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0)), srid=4326)
        cls.open_layer = MapLayer.objects.create(name="Open layer", slug="open-layer")
        cls.board_layer = keep(MapLayer.objects.create(name="Board layer", slug="board-layer",
                                                       owner=cls.owner_person), cls.board_domain)
        cls.nobodys_layer = keep(MapLayer.objects.create(name="Nobodys layer", slug="nobodys-layer"),
                                 cls.board_domain)
        cls.idle_layer = MapLayer.objects.create(name="Idle layer", slug="idle-layer",
                                                 is_active=False)
        for layer in (cls.open_layer, cls.board_layer, cls.nobodys_layer, cls.idle_layer):
            MapLayerPolygon.objects.create(layer=layer, geometry=square, center=square.centroid,
                                           value=1.0, name=f"{layer.name} cell")

        cls.readers = (cls.creator, cls.member, cls.both, cls.senior, cls.guildsman,
                       cls.stranger, cls.staff, cls.root, cls.owner)
        cls.routes = (cls.open, cls.kept, cls.double, cls.orphan)
        cls.layers = (cls.open_layer, cls.board_layer, cls.nobodys_layer, cls.idle_layer)

    @staticmethod
    def names(queryset):
        return set(queryset.values_list("name", flat=True))

    def other_kinds(self):
        """An open and a kept (internal domain) address, zone and territory."""
        self.open_address = Address.objects.create(street="Open St", locality_name="Gdańsk",
                                                   latitude=54.35, longitude=18.65)
        self.kept_address = keep(Address.objects.create(street="Kept St", locality_name="Gdańsk",
                                                        latitude=54.36, longitude=18.66,
                                                        created_by=self.creator),
                                 self.board_domain)
        self.open_territory = Territory.objects.create(name="OpenLand", geometry=SQUARE)
        self.kept_territory = keep(Territory.objects.create(name="KeptLand", geometry=SQUARE,
                                                            capital=self.kept_address),
                                   self.board_domain)
        self.open_zone = Zone.objects.create(name="OpenZone", geometry=MULTI_SQUARE,
                                             territory=self.kept_territory)
        self.kept_zone = keep(Zone.objects.create(name="KeptZone", geometry=MULTI_SQUARE),
                              self.board_domain)


class ReadingRuleTests(ClearanceFixture):
    def test_a_stranger_reads_only_the_open_routes(self):
        self.assertEqual(self.names(access.readable_routes(self.stranger)), {"OpenRoute"})

    def test_a_route_in_two_kept_domains_needs_a_clearance_of_each(self):
        self.assertEqual(self.names(access.readable_routes(self.member)),
                         {"OpenRoute", "BoardRoute", "OrphanRoute"})
        self.assertEqual(self.names(access.readable_routes(self.senior)), {"OpenRoute"})
        self.assertEqual(self.names(access.readable_routes(self.both)),
                         {"OpenRoute", "BoardRoute", "DoubleRoute", "OrphanRoute"})
        self.assertFalse(access.may_read(self.member, self.double))
        self.assertTrue(access.may_read(self.both, self.double))

    def test_a_route_in_two_kept_domains_is_listed_once_for_a_holder_of_both(self):
        rows = list(access.readable_routes(self.both).values_list("name", flat=True))
        self.assertEqual(rows.count("DoubleRoute"), 1)

    def test_one_domain_with_two_clearances_opens_to_a_holder_of_either(self):
        route = keep(Route.objects.create(name="EitherRoute", geometry=self.open.geometry),
                     self.either_domain)
        for user in (self.member, self.senior, self.both):
            with self.subTest(user=user.username):
                self.assertTrue(access.may_read(user, route))
        self.assertFalse(access.may_read(self.stranger, route))

    def test_a_domain_with_no_clearances_keeps_nothing(self):
        route = keep(Route.objects.create(name="LooseRoute", geometry=self.open.geometry),
                     self.loose_domain)
        self.assertIn("LooseRoute", self.names(access.readable_routes(self.stranger)))
        self.assertTrue(access.may_read(AnonymousUser(), route))
        keep(route, self.board_domain)                    # a kept domain besides: kept
        self.assertFalse(access.may_read(self.stranger, route))
        self.assertTrue(access.may_read(self.member, route))

    def test_the_creator_does_not_read_their_kept_route(self):
        self.assertEqual(self.names(access.readable_routes(self.creator)), {"OpenRoute"})
        self.assertFalse(access.may_read(self.creator, self.kept))

    def test_the_layer_owner_does_not_read_their_kept_layer(self):
        self.assertNotIn("Board layer", self.names(access.readable_layers(self.owner)))
        self.assertFalse(access.may_read(self.owner, self.board_layer))

    def test_staff_and_a_community_are_not_clearances(self):
        for user in (self.staff, self.guildsman):
            with self.subTest(user=user.username):
                self.assertEqual(self.names(access.readable_routes(user)), {"OpenRoute"})
                self.assertFalse(access.may_read(user, self.kept))

    def test_a_superuser_reads_every_item(self):
        self.other_kinds()
        self.assertEqual(access.readable_routes(self.root).count(), len(self.routes))
        self.assertEqual(access.readable_layers(self.root).count(), len(self.layers))
        self.assertEqual(access.readable_addresses(self.root).count(), 2)
        self.assertEqual(access.readable_zones(self.root).count(), 2)
        self.assertEqual(access.readable_territories(self.root).count(), 2)

    def test_an_anonymous_visitor_and_nobody_read_only_what_is_open(self):
        for user in (AnonymousUser(), None):
            with self.subTest(user=user):
                self.assertEqual(self.names(access.readable_routes(user)), {"OpenRoute"})
                self.assertEqual(self.names(access.readable_layers(user)),
                                 {"Open layer", "Idle layer"})
                self.assertFalse(access.may_read(user, self.kept))
                self.assertTrue(access.may_read(user, self.open))

    def test_the_given_queryset_is_narrowed_not_replaced(self):
        narrowed = access.readable_routes(self.member, Route.objects.filter(name__startswith="Board"))
        self.assertEqual(self.names(narrowed), {"BoardRoute"})
        self.assertEqual(self.names(access.readable_layers(
            self.member, MapLayer.objects.filter(is_active=False))), {"Idle layer"})

    def test_addresses_zones_and_territories_follow_the_same_rule(self):
        self.other_kinds()
        cases = (
            (access.readable_addresses, {"Open St"}, {"Open St", "Kept St"}, "street"),
            (access.readable_zones, {"OpenZone"}, {"OpenZone", "KeptZone"}, "name"),
            (access.readable_territories, {"OpenLand"}, {"OpenLand", "KeptLand"}, "name"),
        )
        for readable, open_names, all_names, field in cases:
            with self.subTest(readable=readable.__name__):
                for user in (self.stranger, self.creator, self.staff, self.senior, None):
                    self.assertEqual(set(readable(user).values_list(field, flat=True)), open_names)
                for user in (self.member, self.both, self.root):
                    self.assertEqual(set(readable(user).values_list(field, flat=True)), all_names)

    def test_may_read_agrees_with_the_listings_for_every_reader_and_kind(self):
        self.other_kinds()
        listings = (
            (access.readable_routes, self.routes),
            (access.readable_layers, self.layers),
            (access.readable_addresses, (self.open_address, self.kept_address)),
            (access.readable_zones, (self.open_zone, self.kept_zone)),
            (access.readable_territories, (self.open_territory, self.kept_territory)),
        )
        for user in (*self.readers, AnonymousUser()):
            for readable, items in listings:
                pks = set(readable(user).values_list("pk", flat=True))
                for item in items:
                    with self.subTest(user=str(user), item=str(item)):
                        self.assertEqual(access.may_read(user, item), item.pk in pks)

    def test_a_route_chain_is_in_no_domain(self):
        chain = RouteChain.objects.create(name="Coast")
        self.assertTrue(access.may_read(self.stranger, chain))
        self.assertTrue(access.may_read(None, chain))

    def test_clearing_a_domains_clearances_or_deleting_it_opens_its_items(self):
        self.board_domain.clearance_rows.all().delete()
        self.assertTrue(access.may_read(self.stranger, self.kept))
        self.assertFalse(access.may_read(self.stranger, self.double))    # the seniors' still
        self.senior_domain.delete()
        self.assertTrue(access.may_read(self.stranger, self.double))
        self.assertTrue(Route.objects.filter(pk=self.double.pk).exists())

    def test_a_clearance_that_keeps_a_domain_cannot_be_deleted(self):
        with self.assertRaises(ProtectedError):
            self.seniors.delete()

    def test_the_clearance_is_read_afresh(self):
        """A clearance given after the item was kept counts at once."""
        self.assertFalse(access.may_read(self.owner, self.kept))
        self.owner_person.clearances.add(self.board)
        self.assertTrue(access.may_read(fresh(self.owner), self.kept))


class WritingRuleTests(ClearanceFixture):
    def test_an_anonymous_visitor_writes_nothing(self):
        self.assertFalse(access.may_write(AnonymousUser(), self.open))
        self.assertFalse(access.is_staff(AnonymousUser()))

    def test_only_superusers_on_the_plan_manage_domains(self):
        # Superuser-plan functionality since 2026-10-02 (tests_domains.PlanTests):
        # `bootstrap_plans` puts the superusers there are on the plan.
        from django.apps import apps

        if apps.is_installed("toto.subscriptions"):
            import io

            from django.core.management import call_command

            call_command("bootstrap_plans", stdout=io.StringIO())
        self.assertTrue(access.may_manage_domains(fresh(self.root)))
        for user in (self.staff, self.member, self.both, self.creator, AnonymousUser(), None):
            with self.subTest(user=str(user)):
                self.assertFalse(access.may_manage_domains(user))

    def test_the_per_item_clearance_door_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse("locations:clearances_save", args=["route", self.open.pk])
        self.assertFalse(hasattr(access, "CLEARANCED_KINDS"))
        self.assertFalse(hasattr(access, "may_manage_clearances"))


class DetailPageTests(ClearanceFixture):
    def page(self, user, kind, obj):
        self.client.force_login(user)
        return self.client.get(reverse("locations:location_detail", args=[kind, obj.pk]))

    def test_every_kind_is_missing_for_whoever_its_domains_hide_it_from(self):
        self.other_kinds()
        kept = (("route", self.kept), ("maplayer", self.board_layer),
                ("address", self.kept_address), ("zone", self.kept_zone),
                ("territory", self.kept_territory))
        for kind, obj in kept:
            for user in (self.stranger, self.staff, self.creator, self.owner, self.senior):
                with self.subTest(kind=kind, user=user.username):
                    self.assertEqual(self.page(user, kind, obj).status_code, 404)
            for user in (self.member, self.root):
                with self.subTest(kind=kind, user=user.username):
                    self.assertEqual(self.page(user, kind, obj).status_code, 200)

    def test_the_short_doors_are_missing_too(self):
        self.other_kinds()
        self.client.force_login(self.stranger)
        for url in (reverse("locations:route_detail", args=[self.kept.pk]),
                    reverse("locations:route_review", args=[self.kept.pk]),
                    reverse("locations:address_detail", args=[self.kept_address.pk]),
                    reverse("locations:zone_detail", args=[self.kept_zone.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("locations:zone_detail",
                                                 args=[self.kept_zone.pk])).status_code, 200)

    def test_the_page_has_no_per_item_clearances_section(self):
        response = self.page(self.root, "route", self.kept)
        self.assertNotContains(response, 'data-testid="location-clearances"')
        self.assertNotIn("clearances_kind", response.context)

    def test_a_hidden_items_note_and_metadata_are_missing_even_for_its_creator(self):
        self.other_kinds()
        self.client.force_login(self.creator)
        for kind, obj in (("route", self.kept), ("address", self.kept_address)):
            with self.subTest(kind=kind):
                note = self.client.post(reverse("locations:note_save", args=[kind, obj.pk]),
                                        {"note": "was here"})
                metadata = self.client.post(reverse("locations:metadata_save", args=[kind, obj.pk]),
                                            {"metadata": "{}", "format": "json"})
                self.assertEqual((note.status_code, metadata.status_code), (404, 404))
        self.kept.refresh_from_db()
        self.assertEqual(self.kept.notes, "")

    def test_a_readable_page_leaves_out_what_it_points_at_when_that_is_hidden(self):
        self.other_kinds()
        Route.objects.filter(pk=self.open.pk).update(start_address=self.kept_address,
                                                     end_address=self.open_address)
        response = self.page(self.stranger, "route", self.open)
        fields = dict(response.context["fields"])
        self.assertIsNone(fields["Start address"])
        self.assertEqual(fields["End address"], str(self.open_address))
        self.client.force_login(self.stranger)
        payload = json.loads(self.client.get(reverse("locations:route_detail", args=[self.open.pk]))
                             .context["route_payload_json"])
        self.assertIsNone(payload["start_address"])
        self.assertEqual(payload["end_address"]["id"], self.open_address.pk)
        # A zone in a hidden territory, a territory with a hidden capital.
        self.assertEqual(dict(self.page(self.stranger, "zone", self.open_zone).context["fields"])
                         ["Territory"], None)
        self.assertEqual(dict(self.page(self.member, "zone", self.open_zone).context["fields"])
                         ["Territory"], "KeptLand")
        self.client.force_login(self.stranger)
        zone = json.loads(self.client.get(reverse("locations:zone_detail", args=[self.open_zone.pk]))
                          .context["zone_payload_json"])
        self.assertIsNone(zone["territory"])
        self.assertIsNone(dict(self.page(self.stranger, "territory", self.open_territory)
                               .context["fields"])["Capital"])

    def test_the_route_and_zone_pages_print_nothing_their_json_leaves_out(self):
        """2026-10-02, crown 41: the JSON above was right, and the HTML beside
        it printed a route's ends and a zone's territory straight from the
        relation — "Kept St" with a link to it, "Inside KeptLand"."""
        self.other_kinds()
        Route.objects.filter(pk=self.open.pk).update(start_address=self.kept_address,
                                                     end_address=self.open_address)
        route_url = reverse("locations:route_detail", args=[self.open.pk])
        zone_url = reverse("locations:zone_detail", args=[self.open_zone.pk])
        self.client.force_login(self.stranger)
        route_page = self.client.get(route_url)
        self.assertNotContains(route_page, "Kept St")
        self.assertNotContains(route_page, reverse("locations:address_detail",
                                                   args=[self.kept_address.pk]))
        self.assertContains(route_page, "Open St")
        self.assertNotContains(self.client.get(zone_url), "KeptLand")
        self.client.force_login(self.member)
        self.assertContains(self.client.get(route_url), "Kept St")
        self.assertContains(self.client.get(zone_url), "KeptLand")

    def test_a_chain_page_draws_and_counts_only_the_readable_routes(self):
        chain = RouteChain.objects.create(name="Coast")
        Route.objects.filter(pk__in=[self.open.pk, self.kept.pk]).update(route_chain=chain)
        stranger = self.page(self.stranger, "routechain", chain)
        self.assertIn(("Routes", 1), stranger.context["fields"])
        self.assertEqual(len(json.loads(stranger.context["geometry_json"])["coordinates"]), 1)
        member = self.page(self.member, "routechain", chain)
        self.assertIn(("Routes", 2), member.context["fields"])


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
            self.owner: {"Open layer"},                       # owning is not a clearance
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

    def test_every_kind_is_missing_from_the_map_of_whoever_it_is_hidden_from(self):
        self.other_kinds()
        names = {row["name"] for row in self.payload(self.stranger)[0]}
        for name in ("BoardRoute", "DoubleRoute", "KeptZone", "KeptLand"):
            self.assertNotIn(name, names)
        self.assertNotIn(str(self.kept_address), names)
        self.assertIn(str(self.open_address), names)
        names = {row["name"] for row in self.payload(self.member)[0]}
        for name in ("BoardRoute", "KeptZone", "KeptLand", str(self.kept_address)):
            self.assertIn(name, names)
        self.assertNotIn("DoubleRoute", names)

    def test_a_row_names_nothing_hidden(self):
        self.other_kinds()
        rows = {row["name"]: row for row in self.payload(self.stranger)[0]}
        self.assertEqual(rows["OpenZone"]["detail"], "Standalone zone")
        rows = {row["name"]: row for row in self.payload(self.member)[0]}
        self.assertEqual(rows["OpenZone"]["detail"], "Inside KeptLand")
        self.assertEqual(rows["KeptLand"]["detail"], f"Capital: {self.kept_address}")

    def test_a_route_in_two_kept_domains_is_drawn_once(self):
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
    def get(self, user, name, *args, status=200):
        self.client.force_login(add_to_mesh(user))
        response = self.client.get(reverse(f"locations:{name}", args=args))
        self.assertEqual(response.status_code, status)
        return response.json()

    def names(self, user, kind):
        return {row["name"] for row in self.get(user, "api_map_data")["locations"]
                if row["type"] == kind}

    def test_the_map_api_leaves_out_the_routes_a_caller_may_not_read(self):
        self.assertEqual(self.names(self.stranger, "Route"), {"OpenRoute"})
        self.assertEqual(self.names(self.member, "Route"), {"OpenRoute", "BoardRoute", "OrphanRoute"})
        self.assertEqual(self.names(self.creator, "Route"), {"OpenRoute"})

    def test_the_map_api_leaves_out_every_other_hidden_kind(self):
        self.other_kinds()
        self.assertEqual(self.names(self.stranger, "Zone"), {"OpenZone"})
        self.assertEqual(self.names(self.stranger, "Territory"), {"OpenLand"})
        self.assertEqual(self.names(self.stranger, "Address"), {str(self.open_address)})
        self.assertEqual(self.names(self.member, "Territory"), {"OpenLand", "KeptLand"})

    def test_the_zone_and_address_lists_leave_out_the_hidden(self):
        self.other_kinds()
        zones = self.get(self.stranger, "api_zone_list")["zones"]
        self.assertEqual([(z["name"], z["territory_name"]) for z in zones], [("OpenZone", None)])
        zones = {z["name"]: z for z in self.get(self.member, "api_zone_list")["zones"]}
        self.assertEqual(zones["OpenZone"]["territory_name"], "KeptLand")
        streets = {a["street"] for a in self.get(self.stranger, "api_address_list")["addresses"]}
        self.assertEqual(streets, {"Open St"})

    def test_a_hidden_address_answers_404_by_id(self):
        self.other_kinds()
        self.get(self.stranger, "api_address_detail", self.kept_address.pk, status=404)
        self.assertEqual(self.get(self.member, "api_address_detail", self.kept_address.pk)["street"],
                         "Kept St")

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


class AddressPickerTests(ClearanceFixture):
    """The route search's address pickers and the route save offer only the
    addresses the member may read."""

    def setUp(self):
        self.other_kinds()

    def test_the_route_search_offers_only_readable_addresses(self):
        self.client.force_login(self.stranger)
        options = self.client.get(reverse("locations:route_search")).context["address_options"]
        self.assertEqual({o["id"] for o in options}, {str(self.open_address.pk)})
        self.client.force_login(self.member)
        options = self.client.get(reverse("locations:route_search")).context["address_options"]
        self.assertEqual({o["id"] for o in options},
                         {str(self.open_address.pk), str(self.kept_address.pk)})

    def test_a_hidden_address_is_not_available_as_an_end(self):
        from toto.locations import views

        with self.assertRaisesMessage(ValueError, "Start address is not available."):
            views.selected_address_coordinates(self.kept_address.pk, "Start", self.stranger)
        self.assertEqual(views.selected_address_coordinates(self.kept_address.pk, "Start", self.member),
                         (18.66, 54.36))

    def test_saving_a_route_ignores_a_hidden_end(self):
        self.client.force_login(self.stranger)
        self.client.post(reverse("locations:route_save"), {
            "name": "Sneaky", "start_address": self.kept_address.pk,
            "end_address": self.open_address.pk,
            "route_json": json.dumps({"type": "LineString", "coordinates": [[18.6, 54.3], [21.0, 52.2]]}),
        })
        route = Route.objects.get(name="Sneaky")
        self.assertIsNone(route.start_address)
        self.assertEqual(route.end_address, self.open_address)


class OtherDoorsKeepTheirRulesTests(ClearanceFixture):
    """Addresses are gated on the LOCATIONS doors only: a person's profile, a
    community and an event show an address by their own rules."""

    def setUp(self):
        self.other_kinds()

    def test_your_own_profile_shows_your_kept_address(self):
        person = Person.objects.create(user=self.stranger, display_name="Stranger",
                                       slug="stranger", address=self.kept_address)
        self.client.force_login(fresh(self.stranger))
        response = self.client.get(reverse("socialhub:profile_details", args=[person.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["address_shown"], str(self.kept_address))

    def test_an_event_shows_its_kept_venue(self):
        from datetime import timedelta

        from django.utils import timezone

        from toto.events.models import ScheduledEvent

        start = timezone.now() + timedelta(days=1)
        event = ScheduledEvent.objects.create(title="Meeting", start_time=start,
                                              end_time=start + timedelta(hours=1),
                                              address=self.kept_address)
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("events:event_plan", args=[event.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.kept_address))

    def test_a_community_keeps_its_kept_headquarters(self):
        community = Community.objects.create(name="Harbour", slug="harbour",
                                             location=self.kept_address)
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("socialhub:community_detail", args=[community.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, str(self.kept_address))
