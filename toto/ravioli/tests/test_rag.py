"""Tests for the GraphRAG builder (toto.ravioli.rag). Pure unit tests — the
neo4j-graphrag retrievers/LLM and the Neo4j client are mocked, so no live graph
or embedding backend is needed."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from django.test import SimpleTestCase

from neo4j import Record
from neo4j_graphrag.types import RawSearchResult

from toto.ravioli import rag


def _rec(text):
    return Record(zip(["text"], [text]))


class RunGraphRAGTests(SimpleTestCase):
    def test_noop_when_ravioli_disabled(self):
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=False), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as MockClient:
            answer, ctx = rag.run_graphrag(llm=object(), query_text="hello")
        self.assertEqual((answer, ctx), ("", ""))
        MockClient.assert_not_called()  # never touches Neo4j when disabled

    def test_noop_on_empty_query(self):
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True):
            self.assertEqual(rag.run_graphrag(llm=object(), query_text="   "), ("", ""))

    def test_happy_path_returns_answer_and_context(self):
        fake_resp = SimpleNamespace(
            answer="grounded answer",
            retriever_result=SimpleNamespace(items=[
                SimpleNamespace(content="chunk A"), SimpleNamespace(content="chunk B"),
            ]),
        )
        graphrag_instance = mock.Mock()
        graphrag_instance.search.return_value = fake_resp
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as MockClient, \
             mock.patch("toto.ravioli.rag.build_retriever", return_value="RETRIEVER"), \
             mock.patch("toto.ravioli.rag.GraphRAG", return_value=graphrag_instance) as MockGraphRAG:
            answer, ctx = rag.run_graphrag(llm="LLM", query_text="q", top_k=7)

        self.assertEqual(answer, "grounded answer")
        self.assertEqual(ctx, "chunk A\nchunk B")
        MockGraphRAG.assert_called_once_with(retriever="RETRIEVER", llm="LLM")
        graphrag_instance.search.assert_called_once_with(
            query_text="q", retriever_config={"top_k": 7}, return_context=True
        )
        MockClient.return_value.close.assert_called_once()  # driver always closed

    def test_failure_is_caught(self):
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as MockClient:
            MockClient.return_value.driver.side_effect = RuntimeError("neo4j down")
            self.assertEqual(rag.run_graphrag(llm="LLM", query_text="q"), ("", ""))
        MockClient.return_value.close.assert_called_once()

    def test_detail_reports_exception_cause(self):
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as MockClient:
            MockClient.return_value.driver.side_effect = RuntimeError("neo4j down")
            out = rag.run_graphrag(llm="LLM", query_text="q", detail=True)
        self.assertEqual(out["answer"], "")
        self.assertIn("neo4j down", out["cause"])
        self.assertEqual(out["graph"], {"nodes": [], "edges": []})

    def test_detail_uses_retriever_errors_and_graph_on_empty_answer(self):
        fake_resp = SimpleNamespace(answer="", retriever_result=SimpleNamespace(items=[]))
        gi = mock.Mock()
        gi.search.return_value = fake_resp
        retriever = SimpleNamespace(
            last_errors=["vector: boom", "text2cypher: bad cypher"],
            last_graph={"nodes": [{"id": "1"}], "edges": []},
        )
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient"), \
             mock.patch("toto.ravioli.rag.build_retriever", return_value=retriever), \
             mock.patch("toto.ravioli.rag.GraphRAG", return_value=gi):
            out = rag.run_graphrag(llm="LLM", query_text="q", detail=True)
        self.assertEqual(out["answer"], "")
        self.assertIn("vector: boom", out["cause"])
        self.assertEqual(out["graph"], {"nodes": [{"id": "1"}], "edges": []})


class CompositeRetrieverTests(SimpleTestCase):
    def _composite(self, vc, t2c=None):
        return rag.CompositeRetriever(mock.MagicMock(), vector_cypher=vc, text2cypher=t2c)

    def test_merges_vector_and_text2cypher_records(self):
        vc = mock.Mock()
        vc.get_search_results.return_value = RawSearchResult(records=[_rec("v1"), _rec("v2")], metadata={})
        t2c = mock.Mock()
        t2c.get_search_results.return_value = RawSearchResult(records=[_rec("t1")], metadata={})

        out = self._composite(vc, t2c).get_search_results(query_text="q", top_k=5)
        self.assertEqual([r["text"] for r in out.records], ["v1", "v2", "t1"])
        vc.get_search_results.assert_called_once_with(query_text="q", top_k=5)
        t2c.get_search_results.assert_called_once_with(query_text="q")

    def test_isolates_vector_failure(self):
        vc = mock.Mock()
        vc.get_search_results.side_effect = RuntimeError("embeddings off")
        t2c = mock.Mock()
        t2c.get_search_results.return_value = RawSearchResult(records=[_rec("t1")], metadata={})

        out = self._composite(vc, t2c).get_search_results(query_text="q")
        self.assertEqual([r["text"] for r in out.records], ["t1"])  # vector failed, t2c still contributed

    def test_no_text2cypher_returns_vector_only(self):
        vc = mock.Mock()
        vc.get_search_results.return_value = RawSearchResult(records=[_rec("v1")], metadata={})
        out = self._composite(vc, None).get_search_results(query_text="q")
        self.assertEqual([r["text"] for r in out.records], ["v1"])

    def test_none_vector_uses_text2cypher_only(self):
        # When the vector index is missing, vector_cypher is None — Text2Cypher carries it.
        t2c = mock.Mock()
        t2c.get_search_results.return_value = RawSearchResult(records=[_rec("t1")], metadata={})
        out = self._composite(None, t2c).get_search_results(query_text="q")
        self.assertEqual([r["text"] for r in out.records], ["t1"])


class _FakeNode:
    """Duck-typed neo4j Node (Mapping of properties + labels + element_id)."""
    def __init__(self, eid, labels, props):
        self.element_id = eid
        self.labels = frozenset(labels)
        self._p = dict(props)
    def keys(self):
        return self._p.keys()
    def __getitem__(self, k):
        return self._p[k]


class _FakeRel:
    def __init__(self, eid, rtype, start, end):
        self.element_id = eid
        self.type = rtype
        self.start_node = start
        self.end_node = end


class _FakeRecord:
    def __init__(self, values):
        self._v = list(values)
    def values(self):
        return self._v


class ExtractGraphTests(SimpleTestCase):
    def test_extracts_nodes_and_relationships_deduped(self):
        n1 = _FakeNode("a", ["Concept"], {"uid": "u1", "name": "Computer vision"})
        n2 = _FakeNode("b", ["Concept"], {"uid": "u2", "name": "AI"})
        rel = _FakeRel("r1", "REFERENCES", n1, n2)
        # n1 appears twice (dedupe by element_id); rel implies both endpoints
        g = rag._extract_graph([_FakeRecord([n1]), _FakeRecord([n1]), _FakeRecord([rel])])
        self.assertEqual({n["id"] for n in g["nodes"]}, {"a", "b"})
        by_id = {n["id"]: n for n in g["nodes"]}
        self.assertEqual(by_id["a"]["label"], "Computer vision")
        self.assertEqual(by_id["a"]["category"], "Concept")
        self.assertEqual(g["edges"], [{"id": "r1", "source": "a", "target": "b", "label": "REFERENCES"}])

    def test_nodes_only_no_edges(self):
        n1 = _FakeNode("a", ["Concept"], {"name": "X"})
        g = rag._extract_graph([_FakeRecord([n1])])
        self.assertEqual(len(g["nodes"]), 1)
        self.assertEqual(g["edges"], [])

    def test_empty(self):
        self.assertEqual(rag._extract_graph([]), {"nodes": [], "edges": []})


class BuildRetrieverTests(SimpleTestCase):
    def test_skips_vector_when_index_missing(self):
        # VectorCypherRetriever validates its index at construction; a missing index
        # must NOT abort the pipeline — fall back to Text2Cypher only.
        with mock.patch("toto.ravioli.rag.VectorCypherRetriever",
                        side_effect=Exception("No index with name toto_chunk_embeddings found")), \
             mock.patch("toto.ravioli.rag.Text2CypherRetriever", return_value="T2C"):
            r = rag.build_retriever(mock.MagicMock(), llm="LLM", text2cypher=True)
        self.assertIsNone(r._vector_cypher)
        self.assertEqual(r._text2cypher, "T2C")
