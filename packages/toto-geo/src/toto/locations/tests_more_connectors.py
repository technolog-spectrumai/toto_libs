"""What locations hands to code that is not a page: the workflow connector,
the field-map features, and the plugin registries the map page merges.

A workflow runs as nobody, so its connector reads only the items in no kept
map domain; the field map and a map provider read as the viewer when there is
one, and as nobody when there is not (2026-09-29; map domains 2026-09-30).
"""

from types import SimpleNamespace
from unittest import mock, skipUnless

from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase
from django.urls import reverse

from toto.core.connectors import (
    ConnectorExecutionError,
    execute_connector_type,
    list_connector_types,
    validate_connector_type,
)
from toto.locations.connectors import LocationsReadConnector
from toto.locations.models import HAS_GIS, Address, RouteChain, Territory, Zone
from toto.locations.plugins.context_plugins import LocationContextPlugin
from toto.locations.plugins.map_plugins import LocationMapPlugin
from toto.locations.plugins.url_plugins import LocationUrlPlugin
from toto.locations.tests_more_clearances import ClearanceFixture, keep


def run(config, input_data=None):
    return execute_connector_type("locations_read", config, input_data or {})


class ConnectorContractTests(SimpleTestCase):
    def test_the_connector_is_registered_under_its_type(self):
        types = {row["connector_type"]: row for row in list_connector_types()}
        self.assertEqual(types["locations_read"]["app_label"], "locations")

    def test_every_resource_it_names_validates(self):
        for resource in LocationsReadConnector.allowed_resources:
            with self.subTest(resource=resource):
                self.assertEqual(validate_connector_type(
                    "locations_read", {"resource": resource, "action": "list"}), [])

    def test_an_unknown_resource_or_action_is_named_in_the_errors(self):
        errors = validate_connector_type("locations_read", {"resource": "person", "action": "drop"})
        self.assertEqual(len(errors), 2)
        self.assertIn("Connector action must be one of", errors[0])
        self.assertIn("address, map_layer, route, route_chain, territory", errors[1])

    def test_executing_an_unknown_resource_refuses(self):
        with self.assertRaises(ValueError):
            LocationsReadConnector(config={"resource": "person"}).execute({})


@skipUnless(HAS_GIS, "routes and layers draw on geometry")
class ConnectorReadsAsNobodyTests(ClearanceFixture):
    def test_the_route_list_is_the_open_routes_only(self):
        names = [row["name"] for row in run({"resource": "route"})["data"]["routes"]]
        self.assertEqual(names, ["OpenRoute"])

    def test_a_kept_route_cannot_be_fetched_by_id(self):
        with self.assertRaises(ConnectorExecutionError) as caught:
            run({"resource": "route", "action": "get", "value": self.kept.pk})
        self.assertIn("not found", str(caught.exception))

    def test_an_open_route_is_fetched_with_its_geometry(self):
        route = run({"resource": "route", "action": "get", "id_field": "route_id"},
                    {"route_id": self.open.pk})["data"]["route"]
        self.assertEqual((route["id"], route["name"]), (self.open.pk, "OpenRoute"))
        self.assertEqual(route["geometry"]["type"], "MultiLineString")

    def test_searching_does_not_find_a_kept_route_by_its_name(self):
        found = run({"resource": "route", "action": "search", "query": "Board"})["data"]["routes"]
        self.assertEqual(found, [])
        found = run({"resource": "route", "action": "search", "query": "Open"})["data"]["routes"]
        self.assertEqual([row["name"] for row in found], ["OpenRoute"])

    def test_an_empty_search_finds_nothing(self):
        self.assertEqual(run({"resource": "route", "action": "search"})["data"]["routes"], [])

    def test_the_layer_list_is_the_open_layers_only(self):
        names = [row["name"] for row in run({"resource": "map_layer"})["data"]["map_layers"]]
        self.assertEqual(names, ["Idle layer", "Open layer"])

    def test_a_kept_layer_cannot_be_fetched_by_slug(self):
        with self.assertRaises(ConnectorExecutionError):
            run({"resource": "map_layer", "action": "get", "value": "board-layer"})
        layer = run({"resource": "map_layer", "action": "get", "value": "open-layer"})
        self.assertEqual(layer["data"]["map_layer"]["slug"], "open-layer")

    def test_a_layer_names_its_owner_minimally(self):
        from toto.locations.models import MapLayer

        MapLayer.objects.filter(pk=self.open_layer.pk).update(owner=self.owner_person)
        layer = run({"resource": "map_layer", "action": "get", "value": "open-layer"})
        self.assertEqual(set(layer["data"]["map_layer"]["owner"]), {"id", "uid", "display_name", "slug"})

    def test_the_routes_config_is_passed_through(self):
        answer = run({"resource": "route", "route": "next"})
        self.assertEqual(answer["route"], "next")


@skipUnless(HAS_GIS, "territories carry geometry")
class ConnectorOpenResourcesTests(ClearanceFixture):
    def test_addresses_are_listed_searched_and_fetched(self):
        gdansk = Address.objects.create(street="Długa", building="1", locality_name="Gdańsk",
                                        country_name="PL", latitude=54.35, longitude=18.65)
        Address.objects.create(street="Floriańska", locality_name="Kraków", country_name="PL")
        listed = run({"resource": "address"})["data"]["addresses"]
        self.assertEqual([row["locality_name"] for row in listed], ["Gdańsk", "Kraków"])
        found = run({"resource": "address", "action": "search", "q": "gdań"})["data"]["addresses"]
        self.assertEqual([row["id"] for row in found], [gdansk.pk])
        got = run({"resource": "address", "action": "get", "value": gdansk.pk})["data"]["address"]
        self.assertEqual(got["label"], "Długa 1, Gdańsk, PL")
        self.assertEqual(got["geometry"]["coordinates"], [18.65, 54.35])

    def test_the_list_is_capped_by_the_limit(self):
        for n in range(3):
            Address.objects.create(street=f"Street {n}", locality_name="Town")
        self.assertEqual(len(run({"resource": "address", "limit": 2})["data"]["addresses"]), 2)

    def test_a_territory_carries_its_capital(self):
        capital = Address.objects.create(locality_name="Warszawa")
        Territory.objects.create(name="Mazovia", capital=capital,
                                 geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        territory = run({"resource": "territory", "action": "get", "lookup": "name",
                         "value": "Mazovia"})["data"]["territory"]
        self.assertEqual(territory["capital"]["locality_name"], "Warszawa")
        self.assertEqual(territory["geometry"]["type"], "Polygon")

    def test_a_route_chain_counts_its_routes(self):
        chain = RouteChain.objects.create(name="Coast", description="Along the sea")
        self.open.route_chain = chain
        self.open.save(update_fields=["route_chain"])
        chains = run({"resource": "route_chain", "action": "search", "query": "sea"})
        self.assertEqual(chains["data"]["route_chains"][0]["route_count"], 1)

    def test_a_kept_address_territory_and_capital_are_nobodys(self):
        self.other_kinds()
        listed = run({"resource": "address"})["data"]["addresses"]
        self.assertEqual([row["id"] for row in listed], [self.open_address.pk])
        with self.assertRaises(ConnectorExecutionError):
            run({"resource": "address", "action": "get", "value": self.kept_address.pk})
        names = [row["name"] for row in run({"resource": "territory"})["data"]["territories"]]
        self.assertEqual(names, ["OpenLand"])
        Territory.objects.filter(pk=self.open_territory.pk).update(capital=self.kept_address)
        territory = run({"resource": "territory", "action": "get", "value": self.open_territory.pk})
        self.assertIsNone(territory["data"]["territory"]["capital"])

    def test_an_open_routes_kept_end_is_left_out(self):
        self.other_kinds()
        self.open.start_address = self.kept_address
        self.open.end_address = self.open_address
        self.open.save()
        route = run({"resource": "route", "action": "get", "value": self.open.pk})["data"]["route"]
        self.assertIsNone(route["start_address"])
        self.assertEqual(route["end_address"]["id"], self.open_address.pk)

    def test_a_route_chain_counts_its_open_routes_only(self):
        chain = RouteChain.objects.create(name="Coast")
        from toto.locations.models import Route

        Route.objects.filter(pk__in=[self.open.pk, self.kept.pk]).update(route_chain=chain)
        got = run({"resource": "route_chain", "action": "get", "value": chain.pk})
        self.assertEqual(got["data"]["route_chain"]["route_count"], 1)

    def test_a_get_without_a_value_refuses(self):
        with self.assertRaises(ConnectorExecutionError):
            run({"resource": "address", "action": "get"})


@skipUnless(HAS_GIS, "the field map reads geometry")
class FieldMapFeatureTests(ClearanceFixture):
    """`locations_map_features` for a field map: the viewer's routes and layers,
    or with no request only the open ones."""

    def features(self, user=None):
        from toto.locations.plugins.field_plugins import locations_map_features

        request = SimpleNamespace(user=user) if user is not None else None
        return locations_map_features(request)

    def layer_of(self, features, layer):
        return {f["properties"]["name"] for f in features if f["properties"]["layer"] == layer}

    def test_a_stranger_gets_the_open_route_and_the_open_active_layer(self):
        features = self.features(self.stranger)
        self.assertEqual(self.layer_of(features, "route"), {"OpenRoute"})
        self.assertEqual({f["properties"]["layer_slug"] for f in features
                          if f["properties"]["layer"] == "map_layer"}, {"open-layer"})

    def test_a_clearance_holder_gets_what_their_clearance_opens(self):
        features = self.features(self.member)
        self.assertEqual(self.layer_of(features, "route"),
                         {"OpenRoute", "BoardRoute", "OrphanRoute"})     # not the double-kept one
        self.assertEqual({f["properties"]["layer_slug"] for f in features
                          if f["properties"]["layer"] == "map_layer"},
                         {"open-layer", "board-layer", "nobodys-layer"})

    def test_no_request_is_nobody(self):
        features = self.features()
        self.assertEqual(self.layer_of(features, "route"), {"OpenRoute"})
        self.assertEqual({f["properties"]["layer_slug"] for f in features
                          if f["properties"]["layer"] == "map_layer"}, {"open-layer"})

    def test_an_anonymous_request_is_nobody_too(self):
        self.assertEqual(self.layer_of(self.features(AnonymousUser()), "route"), {"OpenRoute"})

    def test_kept_territories_zones_and_addresses_are_missing(self):
        self.other_kinds()
        features = self.features(self.stranger)
        self.assertEqual(self.layer_of(features, "territory"), {"OpenLand"})
        self.assertEqual(self.layer_of(features, "zone"), {"OpenZone"})
        zone = next(f for f in features if f["properties"]["layer"] == "zone")
        self.assertEqual(zone["properties"]["territory"], "")               # its territory is kept
        self.assertEqual(self.layer_of(features, "address"), {str(self.open_address)})
        features = self.features(self.member)
        self.assertEqual(self.layer_of(features, "territory"), {"OpenLand", "KeptLand"})
        self.assertEqual(self.layer_of(features, "zone"), {"OpenZone", "KeptZone"})

    def test_territories_zones_and_addresses_in_no_kept_domain_are_everyones(self):
        territory = Territory.objects.create(name="North",
                                             geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        Zone.objects.create(name="Alpha", territory=territory,
                            geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        Address.objects.create(street="Długa", locality_name="Gdańsk", latitude=54.3, longitude=18.6)
        Address.objects.create(street="Nowhere")                             # no point: not drawn
        features = self.features(self.stranger)
        self.assertEqual(self.layer_of(features, "territory"), {"North"})
        zone = next(f for f in features if f["properties"]["layer"] == "zone")
        self.assertEqual(zone["properties"]["territory"], "North")
        self.assertEqual(self.layer_of(features, "address"), {"Długa, Gdańsk"})

    def test_the_metrics_section_counts_the_readable_rows(self):
        from toto.locations.plugins.field_plugins import locations_metrics_section

        Address.objects.create(locality_name="Gdańsk")
        keep(Address.objects.create(locality_name="Sopot"), self.board_domain)
        section = locations_metrics_section()
        self.assertEqual(section["key"], "locations")
        kpis = {str(k["label"]): k["value"] for k in section["kpis"]}
        self.assertEqual((kpis["Addresses"], kpis["Routes"]), (1, 1))      # nobody: the open ones
        section = locations_metrics_section(SimpleNamespace(user=self.both))
        kpis = {str(k["label"]): k["value"] for k in section["kpis"]}
        self.assertEqual((kpis["Addresses"], kpis["Routes"]), (2, 4))


class MapProviderRegistryTests(SimpleTestCase):
    def test_a_provider_that_takes_the_request_is_given_it(self):
        seen = []

        def viewer_aware(request):
            seen.append(request)
            return [{"name": "mine"}]

        request = object()
        with mock.patch.object(LocationMapPlugin, "_providers", [viewer_aware]):
            self.assertEqual(LocationMapPlugin.get_items(request), [{"name": "mine"}])
        self.assertEqual(seen, [request])

    def test_an_older_provider_is_called_bare(self):
        def everyones():
            return [{"name": "shared"}]

        with mock.patch.object(LocationMapPlugin, "_providers", [everyones]):
            self.assertEqual(LocationMapPlugin.get_items(object()), [{"name": "shared"}])

    def test_no_request_reaches_a_viewer_aware_provider_as_none(self):
        seen = []
        with mock.patch.object(LocationMapPlugin, "_providers",
                               [lambda request=None: seen.append(request) or []]):
            self.assertEqual(LocationMapPlugin.get_items(), [])
        self.assertEqual(seen, [None])

    def test_items_keep_the_providers_order(self):
        providers = [lambda: [{"n": 1}, {"n": 2}], lambda request: [{"n": 3}]]
        with mock.patch.object(LocationMapPlugin, "_providers", providers):
            self.assertEqual([i["n"] for i in LocationMapPlugin.get_items(None)], [1, 2, 3])

    def test_register_appends(self):
        with mock.patch.object(LocationMapPlugin, "_providers", []):
            LocationMapPlugin.register(list)
            self.assertEqual(LocationMapPlugin._providers, [list])


class ContextAndUrlRegistryTests(SimpleTestCase):
    def test_context_providers_merge_later_winning(self):
        with mock.patch.object(LocationContextPlugin, "_providers", []):
            LocationContextPlugin.register(lambda: {"a": 1, "b": 1})
            LocationContextPlugin.register(lambda: {"b": 2})
            self.assertEqual(LocationContextPlugin.get_context(), {"a": 1, "b": 2})

    def test_an_unregistered_url_is_none(self):
        with mock.patch.dict(LocationUrlPlugin._resolvers, clear=True):
            self.assertIsNone(LocationUrlPlugin.get_url("travel_create"))
            self.assertIsNone(LocationUrlPlugin.get_address_visit_review_url(3))

    def test_a_registered_resolver_is_given_the_arguments(self):
        with mock.patch.dict(LocationUrlPlugin._resolvers, clear=True):
            LocationUrlPlugin.register("travel_create", lambda: "/travel/new/")
            LocationUrlPlugin.register_address_visit_review(lambda pk: f"/visits/{pk}/")
            self.assertEqual(LocationUrlPlugin.get_url("travel_create"), "/travel/new/")
            self.assertEqual(LocationUrlPlugin.get_address_visit_review_url(7), "/visits/7/")


@skipUnless(HAS_GIS, "the route page reads geometry")
class RoutePageUrlPluginTests(ClearanceFixture):
    def test_the_route_page_offers_a_travel_only_where_a_host_registered_one(self):
        self.client.force_login(self.stranger)
        url = reverse("locations:route_detail", args=[self.open.pk])
        with mock.patch.dict(LocationUrlPlugin._resolvers, clear=True):
            self.assertIsNone(self.client.get(url).context["travel_create_url"])
            LocationUrlPlugin.register("travel_create", lambda: "/travel/new/")
            response = self.client.get(url)
        self.assertEqual(response.context["travel_create_url"], "/travel/new/")
        self.assertEqual(response.context["route"], self.open)
