"""Tests for ravioli.vector_search and the AgentSession vector/fallback behaviour."""
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


def _rag_profile(graph_rag_enabled=True):
    return SimpleNamespace(
        graph_rag_enabled=graph_rag_enabled,
        graph_rag_labels=["Node"],
        graph_rag_max_nodes=3,
        graph_rag_depth=0,
    )


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
        from toto.steven.services.embeddings import EmbeddingUnavailable
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


# ---------------------------------------------------------------------------
# AgentSession.graph_context_for — default backend is legacy_keyword
# ---------------------------------------------------------------------------

class AgentSessionBackendTests(SimpleTestCase):

    @override_settings(STEVEN_GRAPH_RAG_BACKEND="legacy_keyword")
    def test_default_backend_uses_legacy_keyword_not_vector(self):
        """With legacy_keyword, retrieve_vector_context must never be called."""
        from toto.steven.services.agent_session import AgentSession

        class _Session(AgentSession):
            def invoke(self, p, history=None):
                return "ok"

        profile = _rag_profile()
        session = _Session(profile)

        empty_result = MagicMock()
        empty_result.has_context = False
        empty_result.nodes = []
        empty_result.relationships = []
        empty_result.terms = []

        with patch(
            "toto.ravioli.vector_search.retrieve_vector_context"
        ) as mock_vec, patch(
            "toto.steven.services.graph_rag.GraphRagRetriever.retrieve_for_agent",
            return_value=empty_result,
        ):
            session.graph_context_for("some query")

        mock_vec.assert_not_called()

    @override_settings(STEVEN_GRAPH_RAG_BACKEND="ravioli_vector")
    def test_ravioli_vector_backend_calls_vector_search(self):
        from toto.steven.services.agent_session import AgentSession

        class _Session(AgentSession):
            def invoke(self, p, history=None):
                return "ok"

        profile = _rag_profile()
        session = _Session(profile)

        with patch(
            "toto.ravioli.vector_search.retrieve_vector_context",
            return_value="vector context text",
        ) as mock_vec:
            result = session.graph_context_for("some query")

        mock_vec.assert_called_once_with("some query")
        self.assertEqual(result, "vector context text")

    @override_settings(STEVEN_GRAPH_RAG_BACKEND="ravioli_vector")
    def test_falls_back_to_legacy_when_vector_raises(self):
        """Agent run must not fail when vector search is unavailable."""
        from toto.ravioli.vector_search import VectorSearchUnavailable
        from toto.steven.services.agent_session import AgentSession

        class _Session(AgentSession):
            def invoke(self, p, history=None):
                return "ok"

        profile = _rag_profile()
        session = _Session(profile)

        empty_result = MagicMock()
        empty_result.has_context = False
        empty_result.nodes = []
        empty_result.relationships = []
        empty_result.terms = []

        with patch(
            "toto.ravioli.vector_search.retrieve_vector_context",
            side_effect=VectorSearchUnavailable("Neo4j down"),
        ), patch(
            "toto.steven.services.graph_rag.GraphRagRetriever.retrieve_for_agent",
            return_value=empty_result,
        ) as mock_legacy:
            result = session.graph_context_for("some query")

        # Did not raise; fell back to legacy
        self.assertIsInstance(result, str)
        mock_legacy.assert_called_once()

    @override_settings(STEVEN_GRAPH_RAG_BACKEND="ravioli_vector")
    def test_falls_back_to_legacy_when_embedding_disabled(self):
        """EmbeddingUnavailable is caught and falls back to legacy."""
        from toto.steven.services.embeddings import EmbeddingUnavailable
        from toto.steven.services.agent_session import AgentSession

        class _Session(AgentSession):
            def invoke(self, p, history=None):
                return "ok"

        profile = _rag_profile()
        session = _Session(profile)

        empty_result = MagicMock()
        empty_result.has_context = False
        empty_result.nodes = []
        empty_result.relationships = []
        empty_result.terms = []

        with patch(
            "toto.ravioli.vector_search.embed_text",
            side_effect=EmbeddingUnavailable("disabled"),
        ), patch(
            "toto.steven.services.graph_rag.GraphRagRetriever.retrieve_for_agent",
            return_value=empty_result,
        ) as mock_legacy:
            result = session.graph_context_for("some query")

        self.assertIsInstance(result, str)
        mock_legacy.assert_called_once()

    def test_graph_rag_disabled_returns_empty(self):
        """graph_rag_enabled=False always returns '' regardless of backend."""
        from toto.steven.services.agent_session import AgentSession

        class _Session(AgentSession):
            def invoke(self, p, history=None):
                return "ok"

        profile = _rag_profile(graph_rag_enabled=False)
        session = _Session(profile)

        with override_settings(STEVEN_GRAPH_RAG_BACKEND="ravioli_vector"):
            result = session.graph_context_for("anything")

        self.assertEqual(result, "")
