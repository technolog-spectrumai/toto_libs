import json
from contextlib import contextmanager
from io import StringIO
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.bento import graph_service as gs
from toto.bento import registry
from toto.bento.models import BentoCategory, BentoEdgeType


# ---------------------------------------------------------------------------
# template validation
# ---------------------------------------------------------------------------

class TemplateValidationTests(TestCase):
    def test_valid_category_autofills_slug_and_label(self):
        cat = BentoCategory(name="Big Idea", property_schema=[
            {"name": "title", "type": "string", "required": True},
        ])
        cat.full_clean()
        cat.save()
        self.assertEqual(cat.slug, "big-idea")
        self.assertEqual(cat.neo4j_label, "BigIdea")

    def test_invalid_neo4j_label_rejected(self):
        cat = BentoCategory(name="X", neo4j_label="9bad-label")
        with self.assertRaises(ValidationError) as ctx:
            cat.full_clean()
        self.assertIn("neo4j_label", ctx.exception.message_dict)

    def test_reserved_property_name_rejected(self):
        cat = BentoCategory(name="X", neo4j_label="X",
                            property_schema=[{"name": "uid", "type": "string"}])
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_bad_property_type_rejected(self):
        cat = BentoCategory(name="X", neo4j_label="X",
                            property_schema=[{"name": "f", "type": "blob"}])
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_duplicate_property_rejected(self):
        cat = BentoCategory(name="X", neo4j_label="X", property_schema=[
            {"name": "a", "type": "string"}, {"name": "a", "type": "integer"},
        ])
        with self.assertRaises(ValidationError):
            cat.full_clean()

    def test_valid_edge_type_autofills_rel_type(self):
        et = BentoEdgeType(name="Answered By")
        et.full_clean()
        et.save()
        self.assertEqual(et.slug, "answered-by")
        self.assertEqual(et.rel_type, "ANSWERED_BY")

    def test_invalid_rel_type_rejected(self):
        et = BentoEdgeType(name="X", rel_type="lower case")
        with self.assertRaises(ValidationError) as ctx:
            et.full_clean()
        self.assertIn("rel_type", ctx.exception.message_dict)


# ---------------------------------------------------------------------------
# dynamic neomodel registry
# ---------------------------------------------------------------------------

class RegistryTests(SimpleTestCase):
    def test_build_node_class_descriptors(self):
        from neomodel import (
            IntegerProperty, JSONProperty, StringProperty, StructuredNode, UniqueIdProperty,
        )

        cat = BentoCategory(
            name="Idea", slug="idea-reg", neo4j_label="IdeaReg",
            property_schema=[
                {"name": "title", "type": "string", "required": True},
                {"name": "rating", "type": "integer"},
                {"name": "body", "type": "text"},
            ],
        )
        klass = registry.build_node_class(cat)
        self.assertTrue(issubclass(klass, StructuredNode))
        self.assertEqual(klass.__label__, "IdeaReg")
        props = klass.defined_properties(rels=False)
        self.assertIsInstance(props["uid"], UniqueIdProperty)
        self.assertIsInstance(props["extra"], JSONProperty)
        self.assertIsInstance(props["title"], StringProperty)
        self.assertIsInstance(props["rating"], IntegerProperty)
        self.assertTrue(props["title"].required)

    def test_build_edge_class_is_structuredrel(self):
        from neomodel import FloatProperty, StructuredRel

        et = BentoEdgeType(name="Supports", slug="supports-reg", rel_type="SUPPORTS_REG",
                           property_schema=[{"name": "strength", "type": "float"}])
        klass = registry.build_edge_class(et)
        self.assertTrue(issubclass(klass, StructuredRel))
        props = klass.defined_properties(rels=False)
        self.assertIsInstance(props["strength"], FloatProperty)

    def test_reserved_names_skipped_in_schema(self):
        from neomodel import JSONProperty

        cat = BentoCategory(name="Z", slug="z-reg", neo4j_label="ZReg",
                            property_schema=[{"name": "extra", "type": "string"}])
        klass = registry.build_node_class(cat)
        # `extra` stays the JSON bag, not overridden by the (invalid) schema entry.
        self.assertIsInstance(klass.defined_properties(rels=False)["extra"], JSONProperty)


# ---------------------------------------------------------------------------
# graph_service — node/edge CRUD with mocked Neo4j
# ---------------------------------------------------------------------------

class _FakeNode:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.uid = None

    def save(self):
        self.uid = "uid-123"


class _FakeClient:
    def __init__(self):
        self.queries = []

    def run_cypher(self, query, params=None):
        self.queries.append((query, params or {}))
        if "count(n)" in query or "count(r)" in query:
            return [{"total": 3}]
        if "RETURN n AS n" in query:
            return [{"n": {"uid": "u1", "title": "A", "extra": "{}"}, "labels": ["Idea"]}]
        if "CREATE (a)-[r:" in query:
            return [{"id": "e1", "type": "SUPPORTS", "start": "u1", "end": "u2",
                     "props": {"strength": 0.5, "extra": "{}"}}]
        return []

    def close(self):
        pass


@contextmanager
def _fake_client_factory(fake):
    yield fake


class GraphServiceNodeTests(TestCase):
    def setUp(self):
        self.cat = BentoCategory.objects.create(
            name="Idea", slug="idea", neo4j_label="Idea",
            property_schema=[
                {"name": "title", "type": "string", "required": True},
                {"name": "rating", "type": "integer"},
            ],
        )

    def test_create_node_splits_declared_and_extra(self):
        with patch.object(gs, "_require_neomodel"), \
             patch.object(registry, "node_class_for", return_value=_FakeNode):
            node = gs.create_node("idea", {"title": "Hello", "rating": 5, "foo": "bar"})
        self.assertEqual(node["uid"], "uid-123")
        self.assertEqual(node["category_slug"], "idea")
        self.assertEqual(node["properties"]["title"], "Hello")
        self.assertEqual(node["properties"]["extra"], {"foo": "bar"})

    def test_create_node_missing_required_raises(self):
        with patch.object(gs, "_require_neomodel"):
            with self.assertRaises(gs.GraphValidationError):
                gs.create_node("idea", {"rating": 5})

    def test_list_nodes_paginates_and_returns_total(self):
        fake = _FakeClient()
        with patch.object(gs, "_require_graph"), \
             patch.object(gs, "_client", lambda: _fake_client_factory(fake)):
            rows, total = gs.list_nodes(cat_slug="idea", q="a", limit=10, offset=20)
        self.assertEqual(total, 3)
        self.assertEqual(rows[0]["uid"], "u1")
        row_query = [p for q, p in fake.queries if "SKIP" in q][0]
        self.assertEqual(row_query["offset"], 20)
        self.assertEqual(row_query["limit"], 10)

    def test_delete_nodes_batch(self):
        fake = _FakeClient()
        with patch.object(gs, "_require_graph"), \
             patch.object(gs, "_client", lambda: _fake_client_factory(fake)):
            count = gs.delete_nodes(["a", "b", "c"])
        self.assertEqual(count, 3)
        self.assertIn("DETACH DELETE", fake.queries[0][0])
        self.assertEqual(fake.queries[0][1]["uids"], ["a", "b", "c"])


class GraphServiceEdgeTests(TestCase):
    def setUp(self):
        self.idea = BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")
        self.source = BentoCategory.objects.create(name="Source", slug="source", neo4j_label="Source")
        self.et = BentoEdgeType.objects.create(name="Supports", slug="supports", rel_type="SUPPORTS",
                                               property_schema=[{"name": "strength", "type": "float"}])
        self.et.allowed_sources.set([self.idea])
        self.et.allowed_targets.set([self.idea])

    def test_create_edge_rejects_disallowed_target(self):
        def fake_get_node(uid):
            return {"category_slug": "idea" if uid == "u1" else "source",
                    "category_name": "Idea" if uid == "u1" else "Source"}

        with patch.object(gs, "_require_graph"), patch.object(gs, "get_node", fake_get_node):
            with self.assertRaises(gs.GraphValidationError):
                gs.create_edge("supports", "u1", "u2", {"strength": 0.5})

    def test_create_edge_ok(self):
        fake = _FakeClient()

        def fake_get_node(uid):
            return {"category_slug": "idea", "category_name": "Idea"}

        with patch.object(gs, "_require_graph"), patch.object(gs, "get_node", fake_get_node), \
             patch.object(gs, "_client", lambda: _fake_client_factory(fake)):
            edge = gs.create_edge("supports", "u1", "u2", {"strength": 0.5})
        self.assertEqual(edge["id"], "e1")
        self.assertEqual(edge["edge_type_slug"], "supports")

    def test_delete_edges_batch(self):
        fake = _FakeClient()
        with patch.object(gs, "_require_graph"), \
             patch.object(gs, "_client", lambda: _fake_client_factory(fake)):
            count = gs.delete_edges(["e1", "e2"])
        self.assertEqual(count, 2)
        self.assertIn("DELETE r", fake.queries[0][0])
        self.assertEqual(fake.queries[0][1]["ids"], ["e1", "e2"])


# ---------------------------------------------------------------------------
# graph disabled
# ---------------------------------------------------------------------------

@override_settings(RAVIOLI_ENABLED=False)
class GraphDisabledTests(TestCase):
    def setUp(self):
        BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")

    def test_list_nodes_raises_unavailable(self):
        with self.assertRaises(gs.GraphUnavailable):
            gs.list_nodes()

    def test_create_node_raises_unavailable(self):
        with self.assertRaises(gs.GraphUnavailable):
            gs.create_node("idea", {})


# ---------------------------------------------------------------------------
# app guard, views, migration command
# ---------------------------------------------------------------------------

class AppGuardTests(SimpleTestCase):
    def test_ready_requires_ravioli(self):
        cfg = apps.get_app_config("bento")
        with patch.object(apps, "is_installed", return_value=False):
            with self.assertRaises(ImproperlyConfigured):
                cfg.ready()


class ViewPermissionTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_user("bob", password="pw")
        self.cat = BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")

    def test_node_list_requires_login(self):
        resp = self.client.get(reverse("bento:node_list"))
        self.assertEqual(resp.status_code, 302)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_node_list_shows_unavailable_when_graph_off(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("bento:node_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "unavailable")

    def test_category_template_crud_is_pure_sql(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.post(reverse("bento:category_create"), {
            "name": "Question", "slug": "", "neo4j_label": "",
            "description": "", "property_schema": "[]",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BentoCategory.objects.filter(name="Question").exists())

    def test_node_list_happy_path_with_mocked_graph(self):
        self.client.login(username="bob", password="pw")
        with patch.object(gs, "list_nodes", return_value=([], 0)):
            resp = self.client.get(reverse("bento:node_list"))
        self.assertEqual(resp.status_code, 200)

    def test_category_create_form_ships_autopopulate(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("bento:category_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "bento-autopopulate")   # the live-fill script
        self.assertContains(resp, "id_neo4j_label")        # its CamelCase-label target

    def test_edgetype_create_form_ships_autopopulate(self):
        self.client.login(username="bob", password="pw")
        resp = self.client.get(reverse("bento:edgetype_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "bento-autopopulate")
        self.assertContains(resp, "id_rel_type")           # its UPPER_SNAKE target


class AddEdgeViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_user("ann", password="pw")
        self.idea = BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")
        self.et = BentoEdgeType.objects.create(name="Supports", slug="supports", rel_type="SUPPORTS",
                                               property_schema=[{"name": "strength", "type": "float"}])
        self.et.allowed_sources.set([self.idea])
        self.et.allowed_targets.set([self.idea])
        self.client.login(username="ann", password="pw")

    _NODES = ([{"uid": "a", "display": "Node A", "category_name": "Idea", "color": "#fff"},
               {"uid": "b", "display": "Node B", "category_name": "Idea", "color": "#fff"}], 2)

    def test_single_form_lists_types_and_nodes(self):
        with patch.object(gs, "list_nodes", return_value=self._NODES):
            resp = self.client.get(reverse("bento:edge_create"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Supports")        # edge type select
        self.assertContains(resp, "Node A")          # from/to node select
        self.assertContains(resp, 'name="edge_type"')
        self.assertContains(resp, 'name="data"')

    def test_missing_fields_shows_error(self):
        with patch.object(gs, "list_nodes", return_value=self._NODES):
            resp = self.client.post(reverse("bento:edge_create"), {"from_uid": "", "to_uid": "", "edge_type": ""})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Choose an edge type")

    def test_self_edge_rejected(self):
        with patch.object(gs, "list_nodes", return_value=self._NODES):
            resp = self.client.post(reverse("bento:edge_create"),
                                    {"from_uid": "a", "to_uid": "a", "edge_type": "supports"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "cannot link to itself")

    def test_invalid_json_data_shows_error(self):
        with patch.object(gs, "list_nodes", return_value=self._NODES):
            resp = self.client.post(reverse("bento:edge_create"),
                                    {"from_uid": "a", "to_uid": "b", "edge_type": "supports", "data": "{bad"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "valid JSON")

    def test_create_edge_calls_service_and_redirects(self):
        with patch.object(gs, "create_edge", return_value={"id": "e1"}) as mock:
            resp = self.client.post(reverse("bento:edge_create"),
                                    {"from_uid": "a", "to_uid": "b", "edge_type": "supports",
                                     "data": '{"strength": 0.5}'})
        self.assertEqual(resp.status_code, 302)
        mock.assert_called_once_with("supports", "a", "b", {"strength": 0.5})

    def test_link_from_prefills_target(self):
        # "Link from" on node b → ?to=b: the To dropdown should be preselected.
        with patch.object(gs, "list_nodes", return_value=self._NODES):
            resp = self.client.get(reverse("bento:edge_create"), {"to": "b", "origin": "b"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="b" selected')

    def test_origin_redirects_back_to_that_node(self):
        with patch.object(gs, "create_edge", return_value={"id": "e1"}):
            resp = self.client.post(reverse("bento:edge_create"),
                                    {"from_uid": "a", "to_uid": "b", "edge_type": "supports",
                                     "origin": "b", "data": ""})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse("bento:node_detail", kwargs={"uid": "b"}))

    def test_node_detail_splits_relations_to_and_from(self):
        node = {"uid": "n1", "id": "n1", "display": "This", "category_name": "Idea", "category_slug": "idea",
                "color": "#fff", "properties": {"title": "This"}}
        out = {"id": "e1", "source": "n1", "target": "n2", "source_display": "This",
               "target_display": "Node Two", "edge_type_name": "Supports", "color": "#0ea5e9"}
        inc = {"id": "e2", "source": "n3", "target": "n1", "source_display": "Node Three",
               "target_display": "This", "edge_type_name": "About", "color": "#64748b"}
        with patch.object(gs, "get_node", return_value=node), \
             patch.object(gs, "list_edges", return_value=([out, inc], 2)):
            resp = self.client.get(reverse("bento:node_detail", kwargs={"uid": "n1"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Relations to")
        self.assertContains(resp, "Relations from")
        self.assertContains(resp, "Node Two")    # outgoing target shown by name
        self.assertContains(resp, "Node Three")  # incoming source shown by name


class DisplayNameTests(SimpleTestCase):
    def test_joins_configured_properties(self):
        self.assertEqual(gs._display_name({"name": "A", "title": "B"}), "A, B")

    def test_single_present_property(self):
        self.assertEqual(gs._display_name({"title": "B"}), "B")

    def test_uid_fallback_when_no_name_props(self):
        self.assertEqual(gs._display_name({"uid": "abcdef1234"}), "abcdef12")

    def test_uuid_fallback_for_synced_nodes(self):
        self.assertEqual(gs._display_name({"uuid": "zyxw98761234"}), "zyxw9876")

    def test_empty_props(self):
        self.assertEqual(gs._display_name({}), "node")

    @override_settings(BENTO_DISPLAY_NAME_PROPERTIES=["title"])
    def test_respects_settings_key_list(self):
        self.assertEqual(gs._display_name({"name": "A", "title": "B"}), "B")


class SerializeNodeIdTests(TestCase):
    def test_id_prefers_uid(self):
        node = gs._serialize_node({"uid": "abc", "uuid": "u-123", "name": "X"}, ["Concept"])
        self.assertEqual(node["id"], "abc")

    def test_id_falls_back_to_uuid(self):
        node = gs._serialize_node({"uuid": "u-123", "name": "X"}, ["Concept"])
        self.assertEqual(node["id"], "u-123")
        self.assertIsNone(node["uid"])


class TableViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        User.objects.create_user("tab", password="pw")
        self.client.login(username="tab", password="pw")

    def test_node_list_renders_table_and_forwards_filters(self):
        rows = [{"uid": "u1", "id": "u1", "display": "Node A", "category_name": "Idea", "category_slug": "idea"}]
        with patch.object(gs, "list_nodes", return_value=(rows, 1)) as m:
            resp = self.client.get(reverse("bento:node_list"), {"category": "idea", "q": "x"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Node A")        # name column (display) rendered
        self.assertContains(resp, "u1")            # ID column (identifier) rendered
        self.assertContains(resp, "Per page")      # page-size selector
        self.assertEqual(m.call_args.kwargs["cat_slug"], "idea")
        self.assertEqual(m.call_args.kwargs["q"], "x")
        self.assertEqual(m.call_args.kwargs["limit"], 25)
        self.assertEqual(m.call_args.kwargs["offset"], 0)

    def test_node_list_links_use_id_for_synced_uuid_nodes(self):
        # Synced nodes have no uid (only uuid → exposed as `id`); links must use it,
        # otherwise they resolve to /nodes/None/ and 404.
        rows = [{"uid": None, "id": "u-syn", "display": "Ada", "category_name": "Person",
                 "category_slug": "person"}]
        with patch.object(gs, "list_nodes", return_value=(rows, 1)):
            resp = self.client.get(reverse("bento:node_list"))
        self.assertContains(resp, "/nodes/u-syn/")
        self.assertNotContains(resp, "/nodes/None/")

    def test_node_list_pagination_offset_and_per_page(self):
        with patch.object(gs, "list_nodes", return_value=([], 0)) as m:
            self.client.get(reverse("bento:node_list"), {"page": "3", "per_page": "10"})
        self.assertEqual(m.call_args.kwargs["limit"], 10)
        self.assertEqual(m.call_args.kwargs["offset"], 20)

    def test_node_list_bad_per_page_falls_back_to_default(self):
        with patch.object(gs, "list_nodes", return_value=([], 0)) as m:
            self.client.get(reverse("bento:node_list"), {"per_page": "9999"})
        self.assertEqual(m.call_args.kwargs["limit"], 25)

    def test_edge_list_renders_table_and_forwards_filters(self):
        rows = [{"id": "e1", "source": "a", "target": "b", "source_display": "Node A",
                 "target_display": "Node B", "edge_type_name": "Supports"}]
        with patch.object(gs, "list_edges", return_value=(rows, 1)) as m:
            resp = self.client.get(reverse("bento:edge_list"), {"edge_type": "supports", "q": "y", "per_page": "50", "page": "2"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Node A")
        self.assertEqual(m.call_args.kwargs["et_slug"], "supports")
        self.assertEqual(m.call_args.kwargs["q"], "y")
        self.assertEqual(m.call_args.kwargs["limit"], 50)
        self.assertEqual(m.call_args.kwargs["offset"], 50)


class NodeFormViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_user("ned", password="pw")
        self.cat = BentoCategory.objects.create(
            name="Idea", slug="idea", neo4j_label="Idea",
            property_schema=[{"name": "title", "type": "string", "required": True},
                             {"name": "rating", "type": "integer"}],
        )
        self.client.login(username="ned", password="pw")

    def test_create_get_shows_ace_json_skeleton(self):
        resp = self.client.get(reverse("bento:node_create"), {"category": "idea"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'name="data"')       # ACE-backed hidden textarea
        self.assertContains(resp, "metadata-ace")
        self.assertContains(resp, "title")             # skeleton key from schema

    def test_create_post_valid_calls_service(self):
        with patch.object(gs, "create_node", return_value={"uid": "u1"}) as mock:
            resp = self.client.post(reverse("bento:node_create") + "?category=idea",
                                    {"category": "idea", "data": '{"title": "Hi"}'})
        self.assertEqual(resp.status_code, 302)
        mock.assert_called_once_with("idea", {"title": "Hi"})

    def test_create_post_invalid_json_shows_error(self):
        resp = self.client.post(reverse("bento:node_create") + "?category=idea",
                                {"category": "idea", "data": "{bad"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "valid JSON")

    def test_create_post_schema_validation_error_shown(self):
        with patch.object(gs, "create_node", side_effect=gs.GraphValidationError("'title' is required.")):
            resp = self.client.post(reverse("bento:node_create") + "?category=idea",
                                    {"category": "idea", "data": "{}"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "required")

    def test_update_get_seeds_flat_props_and_shows_relations(self):
        node = {"uid": "u1", "display": "Hi", "category_name": "Idea", "category_slug": "idea",
                "properties": {"title": "Hi", "extra": {"foo": "bar"}}}
        with patch.object(gs, "get_node", return_value=node), \
             patch.object(gs, "list_edges", return_value=([], 0)):
            resp = self.client.get(reverse("bento:node_update", kwargs={"uid": "u1"}))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "title")
        self.assertContains(resp, "foo")            # extra merged in flat
        self.assertContains(resp, "Relations to")   # edit page keeps the link panels
        self.assertContains(resp, "Relations from")

    def test_update_post_calls_service(self):
        node = {"uid": "u1", "display": "Hi", "category_name": "Idea", "category_slug": "idea",
                "properties": {"title": "Hi"}}
        with patch.object(gs, "get_node", return_value=node), \
             patch.object(gs, "update_node", return_value=node) as mock:
            resp = self.client.post(reverse("bento:node_update", kwargs={"uid": "u1"}),
                                    {"data": '{"title": "X"}'})
        self.assertEqual(resp.status_code, 302)
        mock.assert_called_once_with("u1", {"title": "X"})


class MigrationCommandTests(TestCase):
    def test_dry_run_no_legacy_tables_is_noop(self):
        out = StringIO()
        call_command("migrate_bento_to_neo4j", "--dry-run", stdout=out)
        self.assertIn("nothing to migrate", out.getvalue())


# ---------------------------------------------------------------------------
# navigation — bento is surfaced as ravioli's "Data" tab at /ravioli/data/
# ---------------------------------------------------------------------------

class ConceptSchemaMigrationTests(TestCase):
    def test_forwards_rewrites_old_schema_but_not_custom(self):
        import importlib

        from django.apps import apps

        mig = importlib.import_module("toto.bento.migrations.0008_concept_note_name_schema")
        old = [{"name": "title", "type": "string", "required": True, "label": "Title"},
               {"name": "body", "type": "text", "required": False, "label": "Body"}]
        custom = [{"name": "headline", "type": "string", "required": True}]
        BentoCategory.objects.create(name="Concept", slug="concept", neo4j_label="Concept", property_schema=old)
        BentoCategory.objects.create(name="Note", slug="note", neo4j_label="Note", property_schema=custom)

        mig.forwards(apps, None)

        concept = BentoCategory.objects.get(slug="concept")
        note = BentoCategory.objects.get(slug="note")
        self.assertEqual([f["name"] for f in concept.property_schema], ["name", "body"])  # rewritten
        self.assertEqual([f["name"] for f in note.property_schema], ["headline"])          # custom untouched


class NavigationTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_user("nav", password="pw")

    def test_node_list_mounted_under_ravioli_data(self):
        self.assertTrue(reverse("bento:node_list").startswith("/ravioli/data/"))

    def test_legacy_bento_root_redirects_to_data(self):
        resp = self.client.get("/bento/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], reverse("bento:node_list"))

    def test_legacy_bento_subpath_redirect_preserves_path(self):
        resp = self.client.get("/bento/nodes/abc123/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/ravioli/data/nodes/abc123/")

    def test_data_page_has_tab_bar_back_to_ravioli(self):
        """The shared tab bar gives a one-click way back to the rest of ravioli."""
        self.client.login(username="nav", password="pw")
        with patch.object(gs, "list_nodes", return_value=([], 0)):
            resp = self.client.get(reverse("bento:node_list"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, reverse("ravioli:query_unified"))
        self.assertContains(resp, reverse("ravioli:search"))
