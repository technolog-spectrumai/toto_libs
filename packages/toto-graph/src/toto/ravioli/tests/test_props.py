"""No embedding leaves Neo4j for a page, an API, a stored row, a file or a prompt
(2026-10-12).

A :Chunk carries its vector (``embedding``, hundreds of floats) and the host's
``embedding_signature`` as ordinary properties; every reader used to pass them on.
These tests hold the rule (``toto.ravioli.props``) and the choke points it is applied
at, with fake nodes in the shape the neo4j driver gives — no live graph.
"""
from __future__ import annotations

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from neo4j import Record

from toto.ravioli import props as P

VECTOR = [round(0.0123 * (i % 7) - 0.04, 4) + 1e-6 for i in range(384)]
ONE = repr(VECTOR[3])  # one of its numbers, as printed
CHUNK = {"text": "pump 3 leaks", "id": "c1", "embedding": VECTOR, "embedding_signature": "hash:384:v1"}


class FakeNode:
    """A neo4j Node as ravioli reads it: labels, ids, and a mapping of properties."""

    def __init__(self, nid, labels, props):
        self.id = nid
        self.element_id = f"4:x:{nid}"
        self.labels = frozenset(labels)
        self._props = dict(props)

    def keys(self):
        return self._props.keys()

    def __getitem__(self, key):
        return self._props[key]

    def __iter__(self):
        return iter(self._props)

    def __len__(self):
        return len(self._props)


class FakeRel(FakeNode):
    def __init__(self, rid, rel_type, start, end, props):
        super().__init__(rid, (), props)
        del self.labels  # a relationship has a type, not labels
        self.type = rel_type
        self.start_node = start
        self.end_node = end


def _chunk(nid=1):
    return FakeNode(nid, ["WEntity", "Chunk"], CHUNK)


class RuleTests(SimpleTestCase):
    def test_known_names_and_any_embedding_name_are_hidden(self):
        props = {
            **CHUNK, "chunk_embedding": [0.1, 0.2], "embedding_vector": "x",
            "embeddingModel": "minilm", "name": "Pump 3",
        }
        self.assertEqual(P.visible_props(props), {"text": "pump 3 leaks", "id": "c1", "name": "Pump 3"})

    def test_a_vector_under_any_name_is_hidden(self):
        props = {"vec": VECTOR, "as_tuple": tuple(VECTOR[:32]), "name": "n"}
        self.assertEqual(P.visible_props(props), {"name": "n"})

    def test_short_lists_ids_and_flags_stay(self):
        props = {
            "coordinates": [24.75, 59.43],
            "ids": list(range(100)),            # integers: ids, not a vector
            "flags": [True, False] * 40,
            "mixed": ["a"] + [0.5] * 40,
            "short": [0.5] * (P.VECTOR_MIN_LENGTH - 1),
        }
        self.assertEqual(P.visible_props(props), props)

    @override_settings(TOTO_VECTOR_EMBEDDING_PROPERTY="vec3", RAVIOLI_HIDDEN_PROPERTIES=["secret_sauce"])
    def test_configured_names_are_hidden_whatever_their_value(self):
        props = {"vec3": [0.1, 0.2, 0.3], "secret_sauce": "s", "name": "n"}
        self.assertEqual(P.visible_props(props), {"name": "n"})

    def test_none_and_a_node_are_accepted(self):
        self.assertEqual(P.visible_props(None), {})
        self.assertEqual(P.visible_props(_chunk()), {"text": "pump 3 leaks", "id": "c1"})

    def test_driver_vector_type_is_hidden(self):
        class Vector:  # the neo4j driver's native vector value, by name
            pass

        self.assertTrue(P.is_vector(Vector()))

    def test_visible_items_scrubs_stored_rows_and_keeps_the_rest(self):
        rows = [{"id": "1", "labels": ["Chunk"], "props": dict(CHUNK)}, {"id": "2"}, "odd"]
        out = P.visible_items(rows)
        self.assertEqual(out[0], {"id": "1", "labels": ["Chunk"], "props": {"text": "pump 3 leaks", "id": "c1"}})
        self.assertEqual(out[1:], [{"id": "2"}, "odd"])
        self.assertIn("embedding", rows[0]["props"])  # a copy: the input is not changed

    def test_printed_vectors_are_collapsed_in_stored_text(self):
        text = f"<Record c=<Node properties={{'text': 'hi', 'embedding': {VECTOR!r}}}>> at [24.75, 59.43]"
        out = P.hide_vectors_in_text(text)
        self.assertNotIn(ONE, out)
        self.assertIn("[vector hidden]", out)
        self.assertIn("[24.75, 59.43]", out)


class ExtractGraphTests(SimpleTestCase):
    """The Knowledge Graph page, its stored results, the Aurora API, the admin viewer
    and the exports are all built from Neo4jClient.extract_graph."""

    def test_nodes_and_relationships_lose_their_vectors(self):
        from toto.ravioli.connection import Neo4jClient

        chunk, doc = _chunk(1), FakeNode(2, ["Document"], {"title": "Manual"})
        rel = FakeRel(9, "PART_OF", chunk, doc, {"weight": 1, "embedding": VECTOR})
        records = [{"c": chunk, "p": rel, "d": doc}, {"all": [chunk, doc]}]
        nodes, edges = Neo4jClient.__new__(Neo4jClient).extract_graph(records)

        by_id = {n["id"]: n for n in nodes}
        self.assertEqual(by_id["1"]["props"], {"text": "pump 3 leaks", "id": "c1"})
        self.assertEqual(by_id["2"]["props"], {"title": "Manual"})
        self.assertEqual(edges[0]["props"], {"weight": 1})
        self.assertNotIn("embedding", json.dumps([nodes, edges]))


class RagTests(SimpleTestCase):
    def test_retrieved_subgraph_has_no_vectors(self):
        from toto.ravioli.rag import _extract_graph

        graph = _extract_graph([Record(zip(["c"], [_chunk()]))])
        self.assertEqual(graph["nodes"][0]["properties"], {"text": "pump 3 leaks", "id": "c1"})

    def test_context_given_to_the_model_has_no_vectors(self):
        from toto.ravioli.rag import CompositeRetriever

        retriever = CompositeRetriever.__new__(CompositeRetriever)
        record = Record(zip(["c", "c.embedding", "score", "names"], [_chunk(), VECTOR, 0.9, ["a", "b"]]))
        item = retriever.default_record_formatter(record)
        self.assertNotIn("embedding", item.content)
        self.assertNotIn(ONE, item.content)
        self.assertIn("pump 3 leaks", item.content)
        self.assertIn("'Chunk'", item.content)
        self.assertIn("score=0.9", item.content)
        self.assertIn("names=['a', 'b']", item.content)

    def test_clean_vector_query_records_print_as_before(self):
        from toto.ravioli.rag import CompositeRetriever

        retriever = CompositeRetriever.__new__(CompositeRetriever)
        record = Record(zip(["text", "rels", "score"], ["hi", ["PART_OF -> Manual"], 0.5]))
        self.assertEqual(retriever.default_record_formatter(record).content, str(record))


class SearchTests(SimpleTestCase):
    def test_keyword_and_fulltext_results(self):
        from toto.ravioli.services.search import _record_to_dict

        record = {"n": _chunk(), "labels": ["Chunk"], "score": 1.5}
        self.assertEqual(_record_to_dict(record, "score")["props"], {"text": "pump 3 leaks", "id": "c1"})

    def test_every_mode_through_normalize(self):
        from toto.ravioli.services.search import _normalize_result

        out = _normalize_result({"labels": ["Chunk"], "props": dict(CHUNK), "score": 0.8})
        self.assertEqual(out["props"], {"text": "pump 3 leaks", "id": "c1"})

    def test_semantic_hits_lose_the_vector_they_were_found_by(self):
        from toto.ravioli import vector_search

        rows = [{"labels": ["Chunk"], "props": dict(CHUNK), "score": 0.91}]
        with mock.patch.object(vector_search, "_shared_vector_query", return_value=rows):
            out = vector_search.retrieve_vector_results("pump")
        self.assertEqual(out[0]["props"], {"text": "pump 3 leaks", "id": "c1"})


class ExportTests(SimpleTestCase):
    def test_neojson_export_has_no_vectors(self):
        from toto.ravioli import neojson

        nodes = [{"id": "1", "labels": ["Chunk"], "props": dict(CHUNK)}]
        edges = [{"id": "r", "type": "PART_OF", "start": "1", "end": "1", "props": {"embedding": VECTOR}}]
        text = neojson.dumps(neojson.from_ravioli(nodes, edges))
        self.assertNotIn("embedding", text)
        self.assertIn("pump 3 leaks", text)

    def test_editor_shows_an_old_file_without_vectors(self):
        from toto.neo_editor.views import _without_embeddings
        from toto.ravioli import neojson

        graph = neojson.new_graph()
        graph.nodes.append(neojson.NeoNode(id="1", labels=["Chunk"], properties=dict(CHUNK)))
        shown = _without_embeddings(neojson.dumps(graph))
        self.assertNotIn("embedding", shown)
        self.assertIn("pump 3 leaks", shown)
        clean = '{"neojson": "1.0", "type": "Graph", "nodes": [], "relationships": []}'
        self.assertEqual(_without_embeddings(clean), clean)  # untouched: nothing hidden
        self.assertEqual(_without_embeddings("{not json"), "{not json")

    def test_analysis_graph_has_no_vectors(self):
        from toto.ravioli.graph_analysis import build_networkx_graph

        try:
            G = build_networkx_graph([{"id": "1", "labels": ["Chunk"], "props": dict(CHUNK)}], [])
        except ImportError:
            self.skipTest("networkx is not installed")
        self.assertNotIn("embedding", G.nodes["1"])
        self.assertEqual(G.nodes["1"]["text"], "pump 3 leaks")

    def test_sync_plan_delete_bucket_has_no_vectors(self):
        from toto.sql_neo4j_sync.planner import compute_diff

        actual = {"Chunk": {"u1": {"label": "Chunk", "uuid": "u1", "props": dict(CHUNK)}}}
        diff, _ = compute_diff({"Chunk": {}}, actual, {}, {})          # a label the plan owns, emptied
        self.assertEqual(diff["nodes"]["delete"][0]["props"], {"text": "pump 3 leaks", "id": "c1"})
        self.assertEqual(diff["nodes"]["delete"][0]["uuid"], "u1")


class StoredRowTests(TestCase):
    """Rows saved before the rule still hold the vectors: read back without them,
    and the migration rewrites them."""

    def setUp(self):
        from toto.ravioli.models import CypherQuery, CypherQueryResult

        self.query = CypherQuery.objects.create(name="whole world", query="MATCH (n) RETURN n")
        self.result = CypherQueryResult.objects.create(
            query=self.query,
            result_nodes=[{"id": "1", "labels": ["Chunk"], "props": dict(CHUNK)}],
            result_edges=[{"id": "r", "type": "T", "start": "1", "end": "1", "props": {"embedding": VECTOR}}],
        )

    def test_cached_result_is_read_without_vectors(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        user = get_user_model().objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(user)
        response = self.client.get(reverse("ravioli:query_cached_data", args=[self.query.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"embedding", response.content)
        self.assertIn(b"pump 3 leaks", response.content)

    def test_analysis_and_export_read_the_cache_without_vectors(self):
        from toto.ravioli.graph_analysis import load_query_graph

        nodes, edges = load_query_graph(self.query.pk)
        self.assertNotIn("embedding", json.dumps([nodes, edges]))

    def test_migration_rewrites_the_rows(self):
        import importlib

        from django.apps import apps

        migration = importlib.import_module("toto.ravioli.migrations.0002_hide_stored_embeddings")
        migration.hide_embeddings(apps, None)
        self.result.refresh_from_db()
        self.assertEqual(self.result.result_nodes[0]["props"], {"text": "pump 3 leaks", "id": "c1"})
        self.assertEqual(self.result.result_edges[0]["props"], {})


class StoredRunTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        self.client.force_login(get_user_model().objects.create_superuser("root", "r@example.com", "pw"))

    def _run(self, slug, data):
        from toto.workflows.models import Workflow, WorkflowNodeRun, WorkflowRun

        wf = Workflow.objects.create(slug=slug, name=slug)
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.COMPLETED)
        node = wf.nodes.create(node_type="predefined_task", label="s", task_name="t")
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=node, status=WorkflowNodeRun.COMPLETED, output_data={"data": data},
        )
        return run

    def test_old_search_run_is_read_without_vectors(self):
        run = self._run("ravioli-graph-search", {"results": [{"labels": ["Chunk"], "props": dict(CHUNK)}]})
        response = self.client.get(reverse("ravioli:search_status", args=[run.pk]))
        self.assertNotIn(b"embedding", response.content)
        self.assertIn(b"pump 3 leaks", response.content)

    def test_old_graphrag_run_is_read_without_vectors(self):
        run = self._run("ravioli-graphrag-describe", {
            "answer": "a",
            "context": f"<Record c=<Node properties={{'text': 'hi', 'v': {VECTOR!r}}}>>",
            "graph": {"nodes": [{"id": "1", "properties": dict(CHUNK)}], "edges": []},
        })
        response = self.client.get(reverse("ravioli:graphrag_describe_status", args=[run.pk]))
        data = json.loads(response.content)
        self.assertNotIn("embedding", json.dumps(data["graph"]))
        self.assertNotIn(ONE, data["context"])
        self.assertIn("[vector hidden]", data["context"])
