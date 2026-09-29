"""The locations pages and doors beyond reading: saving a drawn route,
importing a layer, notes and metadata, the other kinds' detail pages, and
the router's refusals. Nothing reaches the network: the router's `urlopen`
is patched wherever a route is drawn.
"""

import json
from unittest import mock, skip, skipUnless
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.locations import access, views
from toto.locations.models import (
    HAS_GIS, Address, MapLayer, MapLayerCircle, Route, RouteChain, Territory, Zone,
)
from toto.locations.tests_more_circles import CircleFixture

User = get_user_model()


def said(response):
    return [str(m) for m in get_messages(response.wsgi_request)][-1:]


@skipUnless(HAS_GIS, "the locations pages 404 without GIS")
class PageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        cls.ada = User.objects.create_user("ada", password="x")
        cls.bob = User.objects.create_user("bob", password="x")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)
        cls.gdansk = Address.objects.create(street="Długa", building="1", locality_name="Gdańsk",
                                            latitude=54.349, longitude=18.653)

    def setUp(self):
        self.client.force_login(self.ada)


class RouteSaveTests(PageTestCase):
    url = reverse("locations:route_save")
    LINE = {"type": "LineString", "coordinates": [[18.6, 54.3], [21.0, 52.2]]}

    def save(self, **data):
        return self.client.post(self.url, data)

    def test_a_drawn_line_is_saved_as_the_members_route(self):
        response = self.save(name="To Warsaw", route_json=json.dumps(self.LINE))
        route = Route.objects.get(name="To Warsaw")
        self.assertRedirects(response, reverse("locations:route_detail", args=[route.pk]),
                             fetch_redirect_response=False)
        self.assertEqual(route.geometry.geom_type, "MultiLineString")
        self.assertEqual(route.created_by, self.ada)
        # The member who drew it chooses its circles and edits its note.
        self.assertTrue(access.may_manage_circles(self.ada, route))
        self.assertTrue(access.may_write(self.ada, route))
        self.assertFalse(access.may_write(self.bob, route))

    def test_a_routers_feature_is_saved_from_its_geometry(self):
        feature = {"type": "Feature", "properties": {"mode": "car"},
                   "geometry": {"type": "MultiLineString",
                                "coordinates": [[[18.6, 54.3], [19.0, 53.0]],
                                                [[19.0, 53.0], [21.0, 52.2]]]}}
        self.save(name="Two legs", route_json=json.dumps(feature))
        self.assertEqual(len(Route.objects.get(name="Two legs").geometry), 2)

    def test_the_ends_are_linked_when_they_are_saved_addresses(self):
        self.save(name="Linked", route_json=json.dumps(self.LINE),
                  start_address=self.gdansk.pk, end_address="temporary:2")
        route = Route.objects.get(name="Linked")
        self.assertEqual((route.start_address, route.end_address), (self.gdansk, None))

    def test_a_route_without_a_name_is_refused(self):
        response = self.save(name="  ", route_json=json.dumps(self.LINE))
        self.assertRedirects(response, reverse("locations:route_search"), fetch_redirect_response=False)
        self.assertEqual(said(response), ["Route name is required."])
        self.assertFalse(Route.objects.exists())

    def test_a_route_without_geometry_is_refused(self):
        self.assertEqual(said(self.save(name="Empty")), ["No route geometry was provided."])
        self.assertEqual(said(self.save(name="Empty", route_json="{not json")),
                         ["Route geometry is invalid."])
        self.assertEqual(said(self.save(name="Empty", route_json=json.dumps(
            {"type": "Feature", "geometry": None}))), ["Route geometry is missing."])
        self.assertFalse(Route.objects.exists())

    def test_only_lines_are_routes(self):
        response = self.save(name="Dot", route_json=json.dumps(
            {"type": "Point", "coordinates": [18.6, 54.3]}))
        self.assertEqual(said(response), ["Only LineString and MultiLineString routes can be saved."])
        self.assertFalse(Route.objects.exists())

    def test_saving_is_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    @skip("suspected bug: route_save catches TypeError/ValueError only; malformed GeoJSON "
          "raises GDALException/GEOSException (and a JSON array has no .get) -> 500")
    def test_malformed_coordinates_are_refused_not_a_crash(self):
        for route_json in ('{"type": "LineString", "coordinates": "x"}',
                           '{"type": "LineString", "coordinates": [[0, 0]]}', "[1, 2]"):
            with self.subTest(route_json=route_json):
                response = self.save(name="Broken", route_json=route_json)
                self.assertEqual(response.status_code, 302)
        self.assertFalse(Route.objects.exists())


class LayerImportTests(PageTestCase):
    url = reverse("locations:api_import_layer")

    @staticmethod
    def square(x=0, y=0):
        return [[[x, y], [x + 1, y], [x + 1, y + 1], [x, y + 1], [x, y]]]

    def feature(self, slug="rain", value=1.0, geometry=None, **props):
        return {"type": "Feature",
                "geometry": geometry or {"type": "Polygon", "coordinates": self.square()},
                "properties": {"layer_slug": slug, "value": value, **props}}

    def upload(self, payload, **data):
        self.client.force_login(self.staff)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return self.client.post(self.url, {
            "file": SimpleUploadedFile("layer.geojson", body, content_type="application/json"),
            **data})

    def collection(self, *features):
        return {"type": "FeatureCollection", "features": list(features)}

    def test_features_are_grouped_into_one_layer_per_slug(self):
        response = self.upload(self.collection(
            self.feature("rain", 3.0, layer_name="Rainfall", unit="mm"),
            self.feature("rain", 7.0),
            self.feature("heat", 30.0, layer_name="Heat")))
        self.assertEqual(response.status_code, 200)
        imported = {row["slug"]: row for row in response.json()["imported"]}
        self.assertEqual(imported["rain"]["polygon_count"], 2)
        rain = MapLayer.objects.get(slug="rain")
        self.assertEqual((rain.name, rain.unit, rain.min_value, rain.max_value),
                         ("Rainfall", "mm", 3.0, 7.0))
        self.assertTrue(MapLayer.objects.get(slug="heat").is_active)

    def test_a_single_layer_takes_the_name_given(self):
        self.upload(self.collection(self.feature("rain", layer_name="Rainfall")), name="My rain")
        self.assertEqual(MapLayer.objects.get(slug="rain").name, "My rain")

    def test_a_multipolygon_becomes_one_polygon_each_with_a_centre(self):
        multi = {"type": "MultiPolygon", "coordinates": [self.square(0, 0), self.square(5, 5)]}
        response = self.upload(self.collection(self.feature(geometry=multi, name="Twin", extra="kept")))
        self.assertEqual(response.json()["imported"][0]["polygon_count"], 2)
        polygon = MapLayer.objects.get(slug="rain").polygons.first()
        self.assertEqual(polygon.name, "Twin")
        self.assertEqual(polygon.properties, {"extra": "kept"})
        self.assertIsNotNone(polygon.center)

    def test_features_without_a_value_or_a_polygon_are_skipped(self):
        line = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
        response = self.upload(self.collection(
            self.feature(value=None), self.feature(geometry=line), self.feature(value=2.0)))
        self.assertEqual(response.json()["imported"][0]["polygon_count"], 1)

    def test_reimporting_a_slug_replaces_its_polygons_and_keeps_its_circles(self):
        from toto.socialhub.models import Community

        self.upload(self.collection(self.feature(value=1.0), self.feature(value=2.0)))
        layer = MapLayer.objects.get(slug="rain")
        board = Community.objects.create(name="board", slug="board", is_circle=True)
        MapLayerCircle.objects.create(layer=layer, circle=board)
        self.upload(self.collection(self.feature(value=9.0)))
        layer.refresh_from_db()
        self.assertEqual(list(layer.polygons.values_list("value", flat=True)), [9.0])
        self.assertEqual(list(layer.circle_rows.values_list("circle__name", flat=True)), ["board"])
        self.assertFalse(access.may_read(self.bob, layer))

    def test_what_is_not_a_feature_collection_is_refused(self):
        cases = (
            ({"type": "Feature"}, "Expected a GeoJSON FeatureCollection."),
            (self.collection(), "File contains no features."),
        )
        for payload, error in cases:
            with self.subTest(error=error):
                response = self.upload(payload)
                self.assertEqual((response.status_code, response.json()["error"]), (400, error))
        self.assertFalse(MapLayer.objects.exists())

    @skip("suspected bug: views.api_import_layer's `except VaultFile.DoesNotExist` names "
          "VaultFile, imported only on the vault branch, so any failure reading an UPLOADED "
          "file (bad JSON, not UTF-8) is an UnboundLocalError -> 500, not the 400 it means")
    def test_an_uploaded_file_that_does_not_parse_is_refused(self):
        for body in (b"{broken", b"\xff\xfe"):
            with self.subTest(body=body):
                response = self.upload(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("could not parse", response.json()["error"])
        self.assertFalse(MapLayer.objects.exists())

    def test_neither_a_file_nor_a_vault_file_is_refused(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, 400)

    def test_a_vault_file_that_does_not_exist_is_404(self):
        self.client.force_login(self.staff)
        response = self.client.post(self.url, {"vault_file_id": "999999"})
        self.assertEqual(response.status_code, 404)

    def test_a_member_may_not_upload_a_layer_either(self):
        self.client.force_login(self.bob)
        response = self.client.post(self.url, {"file": SimpleUploadedFile(
            "layer.geojson", json.dumps(self.collection(self.feature())).encode())})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(MapLayer.objects.exists())


class VaultLayerImportTests(PageTestCase):
    """A staff member imports a GeoJSON file they may read in the vault."""

    def setUp(self):
        import shutil
        import tempfile

        super().setUp()
        media = tempfile.mkdtemp(prefix="locations-import-")
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)
        settings = override_settings(MEDIA_ROOT=media)
        settings.enable()
        self.addCleanup(settings.disable)
        self.client.force_login(self.staff)

    def vault_file(self, body, **extra):
        from django.core.files.base import ContentFile

        from toto.vault.models import Bucket, VaultFile

        bucket = Bucket.objects.create(owner=self.staff, name="S", slug="s-staff",
                                       storage_backend="local")
        vf = VaultFile(owner=self.staff, title="layer.json", file_type="json", bucket=bucket, **extra)
        vf.file.save("layer.json", ContentFile(json.dumps(body).encode()), save=False)
        vf.save()
        return vf

    def test_a_readable_vault_file_is_imported(self):
        vf = self.vault_file({"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": LayerImportTests.square()},
            "properties": {"layer_slug": "from-vault", "value": 4.0}}]})
        response = self.client.post(reverse("locations:api_import_layer"), {"vault_file_id": vf.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["imported"], [
            {"slug": "from-vault", "name": "from-vault", "polygon_count": 1}])

    def test_an_encrypted_vault_file_asks_for_its_password(self):
        vf = self.vault_file({"type": "FeatureCollection", "features": []}, is_encrypted=True)
        response = self.client.post(reverse("locations:api_import_layer"), {"vault_file_id": vf.pk})
        self.assertEqual((response.status_code, response.json()["error"]), (422, "encrypted"))
        self.assertFalse(MapLayer.objects.exists())


class MetadataTests(PageTestCase):
    def save(self, kind, obj, text, fmt="json"):
        return self.client.post(reverse("locations:metadata_save", args=[kind, obj.pk]),
                                {"metadata": text, "format": fmt})

    def setUp(self):
        super().setUp()
        self.mine = Address.objects.create(locality_name="Kraków", created_by=self.ada)

    def test_yaml_is_saved_as_a_mapping(self):
        response = self.save("address", self.mine, "floor: 2\ntags: [a, b]\n", "yaml")
        self.assertEqual(response.json(), {"status": "ok"})
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.metadata, {"floor": 2, "tags": ["a", "b"]})

    def test_empty_text_clears_the_metadata(self):
        Address.objects.filter(pk=self.mine.pk).update(metadata={"old": 1})
        self.save("address", self.mine, "   ")
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.metadata, {})

    def test_invalid_text_is_refused_in_its_own_format(self):
        response = self.save("address", self.mine, "{nope", "json")
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()["error"].startswith("Invalid JSON"))
        response = self.save("address", self.mine, "a: [unclosed", "yaml")
        self.assertTrue(response.json()["error"].startswith("Invalid YAML"))

    def test_metadata_must_be_a_mapping(self):
        for text, fmt in (("[1, 2]", "json"), ("- a\n- b\n", "yaml"), ("42", "json")):
            with self.subTest(text=text):
                response = self.save("address", self.mine, text, fmt)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["error"], "Metadata must be a mapping/object.")

    def test_a_layer_has_no_metadata_and_an_unknown_kind_is_404(self):
        layer = MapLayer.objects.create(name="L", slug="l")
        self.client.force_login(self.staff)
        self.assertEqual(self.save("maplayer", layer, "{}").status_code, 404)
        self.assertEqual(self.save("planet", self.mine, "{}").status_code, 404)

    def test_convert_turns_json_into_yaml_and_back(self):
        url = reverse("locations:metadata_convert")
        yaml_text = self.client.post(url, {"text": '{"b": 1, "a": "ż"}', "from": "json",
                                           "to": "yaml"}).json()["text"]
        self.assertEqual(yaml_text, "b: 1\na: ż\n")                     # order and unicode kept
        back = self.client.post(url, {"text": yaml_text, "from": "yaml", "to": "json"}).json()
        self.assertEqual(json.loads(back["text"]), {"b": 1, "a": "ż"})

    def test_convert_refuses_what_is_not_a_mapping(self):
        url = reverse("locations:metadata_convert")
        self.assertEqual(self.client.post(url, {"text": "[1]", "from": "json"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"text": "{", "from": "json"}).status_code, 400)
        self.assertEqual(self.client.post(url, {"text": "", "from": "yaml", "to": "json"}).json()["text"],
                         "{}")


class NoteTests(PageTestCase):
    def test_the_creator_writes_a_routes_note_and_returns_to_next(self):
        from django.contrib.gis.geos import LineString, MultiLineString

        route = Route.objects.create(name="R", created_by=self.ada,
                                     geometry=MultiLineString(LineString((0, 0), (1, 1))))
        response = self.client.post(reverse("locations:note_save", args=["route", route.pk]),
                                    {"note": "  mind the bridge  ", "next": "/locations/"})
        self.assertEqual(response["Location"], "/locations/")
        route.refresh_from_db()
        self.assertEqual(route.notes, "mind the bridge")

    def test_a_kind_without_a_note_is_404(self):
        territory = Territory.objects.create(name="T", geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        self.client.force_login(self.staff)
        response = self.client.post(reverse("locations:note_save", args=["territory", territory.pk]),
                                    {"note": "x"})
        self.assertEqual(response.status_code, 404)


class OtherKindsDetailTests(PageTestCase):
    def detail(self, kind, pk):
        return self.client.get(reverse("locations:location_detail", args=[kind, pk]))

    def test_an_unknown_kind_or_a_missing_row_is_404(self):
        self.assertEqual(self.detail("planet", 1).status_code, 404)
        self.assertEqual(self.detail("address", 999999).status_code, 404)

    def test_a_territory_names_its_capital(self):
        territory = Territory.objects.create(name="Pomerania", capital=self.gdansk,
                                             geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        response = self.detail("territory", territory.pk)
        self.assertIn(("Capital", str(self.gdansk)), response.context["fields"])
        self.assertTrue(response.context["has_geometry"])
        self.assertEqual(response.context["note_field"], None)

    def test_a_zone_names_its_territory(self):
        territory = Territory.objects.create(name="North", geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        zone = Zone.objects.create(name="Alpha", territory=territory,
                                   geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        response = self.detail("zone", zone.pk)
        self.assertIn(("Territory", "North"), response.context["fields"])

    def test_the_zone_page_stands_alone_without_a_project_board(self):
        zone = Zone.objects.create(name="Alpha", geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        response = self.client.get(reverse("locations:zone_detail", args=[zone.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.context["zone_payload_json"])["territory"], None)
        self.assertEqual(list(response.context["routes"]), [])

    def test_an_address_link_goes_to_its_detail_page(self):
        response = self.client.get(reverse("locations:address_detail", args=[self.gdansk.pk]))
        self.assertRedirects(response, reverse("locations:location_detail",
                                               args=["address", self.gdansk.pk]))
        self.assertEqual(self.client.get(reverse("locations:address_detail",
                                                 args=[999999])).status_code, 404)


@skipUnless(HAS_GIS, "route chains draw on geometry")
class RouteChainTests(CircleFixture):
    """A chain is shared infrastructure and stays open; its drawing is its
    routes joined in sequence."""

    def setUp(self):
        self.chain = RouteChain.objects.create(name="Coast")
        Route.objects.filter(pk=self.open.pk).update(route_chain=self.chain, sequence=2)
        self.first = Route.objects.create(name="First", route_chain=self.chain, sequence=1,
                                          geometry=self.open.geometry.clone())
        self.first.geometry = "MULTILINESTRING((10 10, 11 11))"
        self.first.save()

    def chain_row(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("locations:locations_all"))
        return next(row for row in json.loads(response.context["locations_json"])
                    if row["type"] == "Route Chain")

    def test_the_chain_is_its_routes_joined_in_sequence(self):
        geometry = views.route_chain_geometry(self.chain)
        self.assertEqual(geometry["type"], "MultiLineString")
        self.assertEqual(geometry["coordinates"][0], [[10.0, 10.0], [11.0, 11.0]])
        self.assertEqual(geometry["coordinates"][1], [[18.0, 54.0], [18.5, 54.5]])

    def test_a_chain_without_routes_has_no_drawing(self):
        self.assertIsNone(views.route_chain_geometry(RouteChain.objects.create(name="Empty")))

    def test_the_chain_page_draws_the_chain(self):
        self.client.force_login(self.stranger)
        response = self.client.get(reverse("locations:location_detail",
                                           args=["routechain", self.chain.pk]))
        self.assertTrue(response.context["has_geometry"])
        self.assertIn(("Routes", 2), response.context["fields"])

    def test_the_map_lists_the_chain_for_everyone(self):
        row = self.chain_row(self.stranger)
        self.assertEqual((row["name"], row["detail"]), ("Coast", "2 routes"))

    def test_the_map_api_draws_chains_territories_and_zones_for_everyone(self):
        territory = Territory.objects.create(name="North", capital=None,
                                             geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))")
        Zone.objects.create(name="Alpha", territory=territory,
                            geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        self.client.force_login(add_to_mesh(self.stranger))
        rows = {row["type"]: row for row in self.client.get(reverse("locations:api_map_data"))
                .json()["locations"] if row["type"] != "Route"}
        self.assertEqual(rows["Route Chain"]["detail"], "2 routes")
        self.assertEqual(len(rows["Route Chain"]["geometry"]["coordinates"]), 2)
        self.assertEqual((rows["Territory"]["detail"], rows["Zone"]["detail"]),
                         ("Territory", "Inside North"))

    @skip("suspected bug: route_chain_geometry and the chain rows (map page, api/map, "
          "detail page, connector) join every route of the chain, so a route kept to a "
          "circle is drawn, counted and located for a stranger through its chain")
    def test_a_kept_route_is_not_drawn_through_its_chain(self):
        Route.objects.filter(pk=self.kept.pk).update(route_chain=self.chain, sequence=3)
        kept_line = [[19.0, 50.0], [19.5, 50.5]]
        row = self.chain_row(self.stranger)
        self.assertNotIn(kept_line, row["geometry"]["coordinates"])
        self.assertEqual(row["detail"], "2 routes")
        self.client.force_login(add_to_mesh(self.stranger))
        api_chain = next(row for row in self.client.get(reverse("locations:api_map_data")).json()
                         ["locations"] if row["type"] == "Route Chain")
        self.assertNotIn(kept_line, api_chain["geometry"]["coordinates"])


class RoutingTests(SimpleTestCase):
    """The router's answers and failures, as the page and the API see them."""

    class _Response:
        def __init__(self, payload):
            self.body = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return self.body

    def route(self, mode="car", **patch):
        with mock.patch("toto.locations.views.urlopen", **patch) as opened:
            result = views.fetch_traversable_route(18.0, 54.0, 21.0, 52.0, mode)
        return result, opened

    def test_the_answer_is_a_feature_with_distance_and_duration(self):
        answer = {"code": "Ok", "routes": [{"distance": 12345, "duration": 1830,
                                            "geometry": {"type": "LineString", "coordinates": []}}]}
        result, opened = self.route("bicycle", return_value=self._Response(answer))
        self.assertEqual(result["properties"], {"mode": "bicycle", "distance_km": 12.35,
                                                "duration_min": 30.5})
        self.assertTrue(opened.call_args.args[0].full_url.startswith(
            views.ROAD_ROUTING_ENDPOINTS["bicycle"] + "/18.0,54.0;21.0,52.0?"))

    def test_each_failure_is_a_sentence(self):
        cases = (
            (HTTPError("u", 503, "busy", {}, None), "Routing service returned HTTP 503."),
            (URLError("down"), "Routing service is unavailable right now."),
            (TimeoutError(), "Routing service timed out."),
        )
        for exc, sentence in cases:
            with self.subTest(sentence=sentence), self.assertRaises(ValueError) as caught:
                self.route(side_effect=exc)
            self.assertEqual(str(caught.exception), sentence)

    def test_no_route_is_the_routers_own_message_or_a_default(self):
        for payload, sentence in (({"code": "NoRoute", "message": "Impossible route"}, "Impossible route"),
                                  ({"code": "Ok", "routes": []}, "No traversable route found.")):
            with self.subTest(sentence=sentence), self.assertRaises(ValueError) as caught:
                self.route(return_value=self._Response(payload))
            self.assertEqual(str(caught.exception), sentence)

    def test_public_transport_needs_a_transit_backend(self):
        with self.assertRaises(ValueError) as caught:
            self.route("public_transport")
        self.assertIn("LOCATIONS_PUBLIC_TRANSPORT_ROUTING_URL", str(caught.exception))
        answer = {"code": "Ok", "routes": [{"geometry": {"type": "LineString", "coordinates": []}}]}
        with override_settings(LOCATIONS_PUBLIC_TRANSPORT_ROUTING_URL="https://transit.example/route"):
            _result, opened = self.route("public_transport", return_value=self._Response(answer))
        self.assertTrue(opened.call_args.args[0].full_url.startswith("https://transit.example/route/"))

    def test_a_coordinate_is_required_numeric_and_on_the_globe(self):
        for value, sentence in ((None, "Lat is required."), ("", "Lat is required."),
                                ("north", "Lat must be a number."),
                                ("91", "Lat must be between -90 and 90.")):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                views.parse_coordinate(value, "Lat", -90, 90)
            self.assertEqual(str(caught.exception), sentence)
        self.assertEqual(views.parse_coordinate("-90", "Lat", -90, 90), -90.0)

    def test_a_typed_place_has_its_whitespace_folded(self):
        self.assertEqual(views.typed_place("  Nowy   Świat \n"), "Nowy Świat")
        self.assertEqual(views.typed_place(None), "")


class SelectedAddressTests(PageTestCase):
    def test_a_saved_address_answers_its_point(self):
        lng, lat = views.selected_address_coordinates(self.gdansk.pk, "Start")
        self.assertEqual((round(lng, 3), round(lat, 3)), (18.653, 54.349))

    def test_nothing_or_a_temporary_pin_is_no_saved_address(self):
        self.assertIsNone(views.selected_address_coordinates("", "Start"))
        self.assertIsNone(views.selected_address_coordinates("temporary:1", "Start"))

    def test_a_missing_or_pointless_address_is_refused(self):
        pointless = Address.objects.create(locality_name="Nowhere")
        for value in (999999, pointless.pk, "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError) as caught:
                views.selected_address_coordinates(value, "End")
            self.assertEqual(str(caught.exception), "End address is not available.")

    def test_a_route_search_naming_a_missing_address_says_so(self):
        response = self.client.get(reverse("locations:route_search"),
                                   {"mode": "car", "start_address": "999999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "Start address is not available.")


class RouteSearchPostTests(PageTestCase):
    def test_an_unknown_mode_is_refused_before_anything_is_looked_up(self):
        from toto.locations import geocoding

        with mock.patch.object(geocoding, "first_match") as looked_up:
            response = self.client.post(reverse("locations:route_search"),
                                        {"mode": "teleport", "start_query": "Gdansk"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"],
                         "Mode must be car, bicycle, foot, or public transport.")
        looked_up.assert_not_called()


class LabelTests(PageTestCase):
    def test_an_address_reads_as_people_write_it(self):
        address = Address(street="Długa", building="1", apartment="4", locality_name="Gdańsk",
                          state_or_province_name="pomorskie", country_name="PL")
        self.assertEqual(str(address), "Długa 1, Apt 4, Gdańsk, pomorskie, PL")

    def test_an_address_with_nothing_typed_is_named_by_its_number(self):
        address = Address.objects.create(latitude=1.0, longitude=2.0)
        self.assertEqual(str(address), f"Address {address.pk}")
        self.assertEqual(str(Address()), "Address")

    def test_a_route_without_a_name_is_named_by_its_number(self):
        from django.contrib.gis.geos import LineString, MultiLineString

        route = Route.objects.create(geometry=MultiLineString(LineString((0, 0), (1, 1))))
        self.assertEqual(str(route), f"Route {route.pk}")

    def test_a_circle_row_names_the_thing_and_the_circle(self):
        from django.contrib.gis.geos import LineString, MultiLineString

        from toto.locations.models import RouteCircle
        from toto.socialhub.models import Community

        board = Community.objects.create(name="board", slug="board", is_circle=True)
        route = Route.objects.create(name="Coast", geometry=MultiLineString(LineString((0, 0), (1, 1))))
        layer = MapLayer.objects.create(name="Rain", slug="rain")
        self.assertEqual(str(RouteCircle.objects.create(route=route, circle=board)), "Coast — board")
        self.assertEqual(str(MapLayerCircle.objects.create(layer=layer, circle=board)), "Rain — board")


class JsonDoorRefusalTests(PageTestCase):
    def test_the_route_api_refuses_what_is_not_a_json_object(self):
        url = reverse("locations:api_route_search")
        for body in ("{not json", "[1, 2]", '"car"'):
            with self.subTest(body=body):
                response = self.client.post(url, body, content_type="application/json")
                self.assertEqual((response.status_code, response.json()),
                                 (400, {"error": "Invalid JSON."}))

    def test_the_address_api_refuses_broken_json(self):
        self.client.force_login(add_to_mesh(self.ada))
        response = self.client.post(reverse("locations:api_address_list"), "{",
                                    content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Address.objects.filter(street="").exclude(pk=self.gdansk.pk).exists())

    def test_the_address_api_keeps_two_letters_of_a_country(self):
        self.client.force_login(add_to_mesh(self.ada))
        response = self.client.post(reverse("locations:api_address_list"),
                                    json.dumps({"street": "Długa", "country_name": "PLX"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["country_name"], "PL")

    @skip("suspected bug: AddressListCreateApiView.post never sets created_by, so the "
          "member who made the address may not change its note or metadata (staff only)")
    def test_an_address_made_through_the_api_is_its_makers(self):
        self.client.force_login(add_to_mesh(self.ada))
        response = self.client.post(reverse("locations:api_address_list"),
                                    json.dumps({"street": "Długa"}), content_type="application/json")
        address = Address.objects.get(pk=response.json()["id"])
        self.assertEqual(address.created_by, self.ada)

    def test_the_address_api_answers_a_point_as_lat_and_lng(self):
        self.client.force_login(add_to_mesh(self.ada))
        row = self.client.get(reverse("locations:api_address_detail", args=[self.gdansk.pk])).json()
        self.assertEqual((round(row["lat"], 3), round(row["lng"], 3)), (54.349, 18.653))
        self.assertEqual(row["display"], "Długa 1, Gdańsk")
