"""Tests for ravioli.vector_search."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, override_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_record(text, score=None):
    r = MagicMock()
    r.get = lambda k, default=None: {"text": text, "score": score}.get(k, default)
    return r


# ---------------------------------------------------------------------------
# retrieve_vector_context
# ---------------------------------------------------------------------------

class VectorSearchTests(SimpleTestCase):

    @override_settings(
        TOTO_NEO4J_VECTOR_INDEX="toto_chunk_embeddings",
        TOTO_VECTOR_TEXT_PROPERTY="text",
    )
    def test_calls_embed_then_neo4j(self):
        mock_vec = [0.1, 0.2, 0.3]

        with patch(
            "toto.ravioli.vector_search.embed_text",
            return_value=mock_vec,
        ) as mock_embed, patch(
            "toto.ravioli.vector_search.Neo4jClient",
        ) as MockClient:
            MockClient.return_value.run_cypher.return_value = [
                _fake_record("relevant chunk", score=0.95)
            ]
            MockClient.return_value.close.return_value = None

            from toto.ravioli.vector_search import retrieve_vector_context
            context = retrieve_vector_context("test query", top_k=3)

        mock_embed.assert_called_once_with("test query")
        cypher_call = MockClient.return_value.run_cypher.call_args
        params = cypher_call[0][1]
        self.assertEqual(params["index"], "toto_chunk_embeddings")
        self.assertEqual(params["k"], 3)
        self.assertEqual(params["vector"], mock_vec)
        self.assertIn("relevant chunk", context)

    @override_settings(
        TOTO_NEO4J_VECTOR_INDEX="toto_chunk_embeddings",
        TOTO_VECTOR_TEXT_PROPERTY="text",
    )
    def test_context_includes_score(self):
        with patch(
            "toto.ravioli.vector_search.embed_text", return_value=[0.5]
        ), patch("toto.ravioli.vector_search.Neo4jClient") as MockClient:
            MockClient.return_value.run_cypher.return_value = [
                _fake_record("chunk a", score=0.88)
            ]
            MockClient.return_value.close.return_value = None

            from toto.ravioli.vector_search import retrieve_vector_context
            context = retrieve_vector_context("q")

        self.assertIn("0.8800", context)

    @override_settings(TOTO_VECTOR_TEXT_PROPERTY="text")
    def test_returns_empty_string_when_no_hits(self):
        with patch(
            "toto.ravioli.vector_search.embed_text", return_value=[0.1]
        ), patch("toto.ravioli.vector_search.Neo4jClient") as MockClient:
            MockClient.return_value.run_cypher.return_value = []
            MockClient.return_value.close.return_value = None

            from toto.ravioli.vector_search import retrieve_vector_context
            result = retrieve_vector_context("nothing")

        self.assertEqual(result, "")

    def test_embedding_unavailable_raises_vector_search_unavailable(self):
        from toto.vicuna.embeddings import EmbeddingUnavailable
        from toto.ravioli.vector_search import VectorSearchUnavailable, retrieve_vector_context

        with patch(
            "toto.ravioli.vector_search.embed_text",
            side_effect=EmbeddingUnavailable("disabled"),
        ):
            with self.assertRaises(VectorSearchUnavailable) as ctx:
                retrieve_vector_context("query")

        self.assertIn("Embedding unavailable", str(ctx.exception))

    def test_neo4j_connection_error_raises_vector_search_unavailable(self):
        from toto.ravioli.connection import Neo4jConnectionError
        from toto.ravioli.vector_search import VectorSearchUnavailable, retrieve_vector_context

        with patch(
            "toto.ravioli.vector_search.embed_text", return_value=[0.1]
        ), patch("toto.ravioli.vector_search.Neo4jClient") as MockClient:
            MockClient.return_value.run_cypher.side_effect = Neo4jConnectionError("down")
            MockClient.return_value.close.return_value = None

            with self.assertRaises(VectorSearchUnavailable) as ctx:
                retrieve_vector_context("query")

        self.assertIn("Neo4j unreachable", str(ctx.exception))

    @override_settings(TOTO_VECTOR_TEXT_PROPERTY="!invalid")
    def test_invalid_text_property_raises_vector_search_unavailable(self):
        from toto.ravioli.vector_search import VectorSearchUnavailable, retrieve_vector_context

        with patch("toto.ravioli.vector_search.embed_text", return_value=[0.1]):
            with self.assertRaises(VectorSearchUnavailable):
                retrieve_vector_context("query")
