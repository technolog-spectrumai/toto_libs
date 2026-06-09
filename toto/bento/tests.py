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
            "description": "", "property_schema": "[]", "color": "#fff", "icon": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(BentoCategory.objects.filter(name="Question").exists())

    def test_node_list_happy_path_with_mocked_graph(self):
        self.client.login(username="bob", password="pw")
        with patch.object(gs, "list_nodes", return_value=([], 0)):
            resp = self.client.get(reverse("bento:node_list"))
        self.assertEqual(resp.status_code, 200)


class MigrationCommandTests(TestCase):
    def test_dry_run_no_legacy_tables_is_noop(self):
        out = StringIO()
        call_command("migrate_bento_to_neo4j", "--dry-run", stdout=out)
        self.assertIn("nothing to migrate", out.getvalue())
