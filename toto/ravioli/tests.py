from django.test import SimpleTestCase, override_settings

from .connection import connection_uris
from .planner import ProjectionPlanApplier, prop_changes, summarize_diff
from .projection import _get_value


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        return []


class ProjectionPlannerTests(SimpleTestCase):
    def test_neo4j_uri_adds_local_debug_fallback_for_docker_host(self):
        self.assertEqual(
            connection_uris("bolt://neo4j:7687"),
            ["bolt://127.0.0.1:7687", "bolt://neo4j:7687"],
        )

    @override_settings(RAVIOLI_NEO4J_LOCAL_FALLBACK=False)
    def test_neo4j_uri_fallback_can_be_disabled(self):
        self.assertEqual(
            connection_uris("bolt://neo4j:7687"),
            ["bolt://neo4j:7687"],
        )

    def test_prop_changes_compares_expected_fields_only(self):
        changes = prop_changes(
            {"title": "New", "body": "Same"},
            {"title": "Old", "body": "Same", "extra": "Ignored"},
        )

        self.assertEqual(
            changes,
            {"title": {"from": "Old", "to": "New"}},
        )

    def test_default_dict_transform_serializes_to_json_property(self):
        class Obj:
            properties = {"rating": 3, "kind": "question", "status": "open"}

        value = _get_value(
            Obj(),
            {"source": "properties", "transform": "default_dict"},
        )

        self.assertEqual(
            value,
            '{"kind": "question", "rating": 3, "status": "open"}',
        )

    def test_summarize_diff_counts_non_ignored_changes(self):
        diff = {
            "nodes": {
                "create": [{"uuid": "1"}],
                "update": [{"uuid": "2"}],
                "delete": [],
                "ignored": [{"uuid": "3"}],
            },
            "relationships": {
                "create": [],
                "update": [{"key": "r1"}],
                "delete": [{"key": "r2"}],
                "ignored": [{"key": "r3"}],
            },
        }

        summary = summarize_diff(diff)

        self.assertEqual(summary["nodes"]["ignored"], 1)
        self.assertEqual(summary["relationships"]["delete"], 1)
        self.assertEqual(summary["total_changes"], 4)

    def test_applier_runs_node_and_relationship_operations(self):
        client = FakeNeo4jClient()
        applier = ProjectionPlanApplier(client)
        diff = {
            "nodes": {
                "create": [
                    {"label": "IdeaBox", "uuid": "box-1", "props": {"title": "A"}},
                ],
                "update": [],
                "delete": [
                    {"label": "IdeaBox", "uuid": "box-2"},
                ],
                "ignored": [],
            },
            "relationships": {
                "create": [
                    {
                        "kind": "direct",
                        "from_label": "IdeaBox",
                        "from_uuid": "box-1",
                        "relation": "HAS_CATEGORY",
                        "to_label": "IdeaCategory",
                        "to_uuid": "cat-1",
                        "props": {},
                    }
                ],
                "update": [],
                "delete": [],
                "ignored": [],
            },
        }

        applier.apply(diff)

        self.assertEqual(len(client.calls), 3)
        self.assertIn("MERGE (n:IdeaBox", client.calls[0][0])
        self.assertIn("MERGE (a)-[r:HAS_CATEGORY]->(b)", client.calls[1][0])
        self.assertIn("DETACH DELETE", client.calls[2][0])

    def test_applier_serializes_dict_props_from_existing_plan(self):
        client = FakeNeo4jClient()
        applier = ProjectionPlanApplier(client)
        diff = {
            "nodes": {
                "create": [
                    {
                        "label": "IdeaBox",
                        "uuid": "box-1",
                        "props": {
                            "properties": {
                                "rating": 3,
                                "kind": "question",
                                "status": "open",
                            },
                        },
                    },
                ],
                "update": [],
                "delete": [],
                "ignored": [],
            },
            "relationships": {
                "create": [],
                "update": [],
                "delete": [],
                "ignored": [],
            },
        }

        applier.apply(diff)

        self.assertEqual(
            client.calls[0][1]["props"]["properties"],
            '{"kind": "question", "rating": 3, "status": "open"}',
        )
