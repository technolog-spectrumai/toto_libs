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

    def test_return_cause_reports_exception(self):
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient") as MockClient:
            MockClient.return_value.driver.side_effect = RuntimeError("neo4j down")
            out = rag.run_graphrag(llm="LLM", query_text="q", return_cause=True)
        self.assertEqual(out[0], "")
        self.assertIn("neo4j down", out[2])

    def test_return_cause_uses_retriever_errors_on_empty_answer(self):
        fake_resp = SimpleNamespace(answer="", retriever_result=SimpleNamespace(items=[]))
        gi = mock.Mock()
        gi.search.return_value = fake_resp
        retriever = SimpleNamespace(last_errors=["vector: boom", "text2cypher: bad cypher"])
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.ravioli.connection.Neo4jClient"), \
             mock.patch("toto.ravioli.rag.build_retriever", return_value=retriever), \
             mock.patch("toto.ravioli.rag.GraphRAG", return_value=gi):
            out = rag.run_graphrag(llm="LLM", query_text="q", return_cause=True)
        self.assertEqual(out[0], "")
        self.assertIn("vector: boom", out[2])
        self.assertIn("text2cypher: bad cypher", out[2])


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
