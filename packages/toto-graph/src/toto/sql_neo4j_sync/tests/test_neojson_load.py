from django.test import SimpleTestCase

from toto.sql_neo4j_sync import graphsync
from toto.ravioli.neojson import NeoGraph, NeoNode, NeoRelationship
from toto.sql_neo4j_sync.planner import junction_relationship_key


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        return []


def sample_graph():
    return NeoGraph(
        nodes=[
            NeoNode(id="p1", labels=["Person"], properties={"name": "Alice"}),
            NeoNode(id="p2", labels=["Person"], properties={"name": "Bob"}),
        ],
        relationships=[
            # direct (FK-style): no id
            NeoRelationship(rel_type="KNOWS", start="p1", end="p2"),
            # junction (carries id → ravioli_uuid + props)
            NeoRelationship(rel_type="MEMBER_OF", start="p1", end="p2", id="j1", properties={"since": 2020}),
        ],
    )


class NeojsonToExpectedTests(SimpleTestCase):
    def test_maps_nodes_and_both_relationship_kinds(self):
        expected_nodes, expected_rels = graphsync.neojson_to_expected(sample_graph())

        self.assertEqual(set(expected_nodes), {"Person"})
        self.assertEqual(
            expected_nodes["Person"]["p1"],
            {"label": "Person", "uuid": "p1", "props": {"name": "Alice"}},
        )

        kinds = sorted(r["kind"] for r in expected_rels.values())
        self.assertEqual(kinds, ["direct", "junction"])

        junction = next(r for r in expected_rels.values() if r["kind"] == "junction")
        self.assertEqual(junction["ravioli_uuid"], "j1")
        self.assertEqual(junction["props"], {"since": 2020})
        self.assertEqual(junction["from_label"], "Person")
        self.assertEqual(junction["to_label"], "Person")
        # key matches the planner's scheme so it diffs against `actual` identically
        self.assertIn(junction_relationship_key(junction), expected_rels)

    def test_node_without_label_is_rejected(self):
        graph = NeoGraph(nodes=[NeoNode(id="x", labels=[], properties={})])
        with self.assertRaises(ValueError):
            graphsync.neojson_to_expected(graph)


class DiffNeojsonModeTests(SimpleTestCase):
    def _actual_with_extra_person(self):
        return {
            "Person": {
                "p1": {"label": "Person", "uuid": "p1", "props": {"name": "Alice"}},
                "p3": {"label": "Person", "uuid": "p3", "props": {"name": "Carol"}},
            }
        }, {}

    def test_merge_never_deletes(self):
        actual_nodes, actual_rels = self._actual_with_extra_person()
        diff, _ = graphsync.diff_neojson(sample_graph(), actual_nodes, actual_rels, mode="merge")
        self.assertEqual(diff["nodes"]["delete"], [])
        self.assertEqual(diff["relationships"]["delete"], [])
        self.assertIn("p2", [n["uuid"] for n in diff["nodes"]["create"]])

    def test_replace_deletes_extra_node_of_present_label(self):
        actual_nodes, actual_rels = self._actual_with_extra_person()
        diff, _ = graphsync.diff_neojson(sample_graph(), actual_nodes, actual_rels, mode="replace")
        self.assertEqual([n["uuid"] for n in diff["nodes"]["delete"]], ["p3"])

    def test_replace_deletes_label_absent_from_document(self):
        # The doc has no Company nodes; replace must still delete the managed one.
        actual_nodes = {"Company": {"c1": {"label": "Company", "uuid": "c1", "props": {}}}}
        diff, _ = graphsync.diff_neojson(sample_graph(), actual_nodes, {}, mode="replace")
        self.assertEqual([n["uuid"] for n in diff["nodes"]["delete"]], ["c1"])
        # merge leaves it untouched
        diff_merge, _ = graphsync.diff_neojson(sample_graph(), actual_nodes, {}, mode="merge")
        self.assertEqual(diff_merge["nodes"]["delete"], [])

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            graphsync.load_neojson(FakeNeo4jClient(), sample_graph(), mode="wipe", configs=[])


class LoadNeojsonWiringTests(SimpleTestCase):
    def test_merge_into_empty_graph_issues_creates_and_no_node_deletes(self):
        client = FakeNeo4jClient()
        # configs=[] → ravioli manages nothing, so `actual` is empty and everything
        # in the document is a create. This exercises the full wiring (planner →
        # diff → applier) without a live Neo4j.
        summary = graphsync.load_neojson(client, sample_graph(), mode="merge", configs=[])

        queries = " ".join(q for q, _ in client.calls)
        self.assertIn("MERGE (n:Person", queries)
        self.assertIn("MERGE (a)-[r:KNOWS]->(b)", queries)
        self.assertNotIn("DETACH DELETE", queries)  # merge never deletes nodes

        self.assertEqual(summary["mode"], "merge")
        self.assertEqual(summary["nodes"]["create"], 2)
        self.assertEqual(summary["relationships"]["create"], 2)
