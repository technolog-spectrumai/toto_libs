"""Tests for ravioli search service and views."""

import json
from unittest.mock import MagicMock, patch

from django.test import RequestFactory, TestCase
from django.urls import reverse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(props, labels=("Chunk",), score=None):
    """Build a minimal fake neo4j Record for testing."""
    node = MagicMock()
    node.labels = list(labels)
    node.__iter__ = lambda self: iter(props.items())
    node.__getitem__ = lambda self, k: props[k]
    node.keys = lambda: list(props.keys())

    record = MagicMock()
    record.get = lambda key, default=None: {
        "labels": list(labels),
        "n": node,
        "node": node,
        "score": score,
    }.get(key, default)
    return record


# ---------------------------------------------------------------------------
# Service: basic_search
# ---------------------------------------------------------------------------

class BasicSearchTests(TestCase):

    @patch("toto.ravioli.services.search._client")
    def test_returns_results(self, mock_client):
        record = _make_record({"text": "hello world", "name": "Test"})
        mock_client.return_value.run_cypher.return_value = [record]

        from toto.ravioli.services.search import basic_search
        results = basic_search("hello", limit=10)

        self.assertEqual(len(results), 1)
        self.assertIn("labels", results[0])
        self.assertIn("props", results[0])

    @patch("toto.ravioli.services.search._client")
    def test_passes_params_not_interpolated(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []

        from toto.ravioli.services.search import basic_search
        basic_search("'; DROP TABLE nodes; --", limit=5)

        call_args = mock_client.return_value.run_cypher.call_args
        # User input must be in the params dict, never in the query string
        query_str, params = call_args[0][0], call_args[0][1]
        self.assertIn("$q", query_str)
        self.assertIn("$limit", query_str)
        self.assertEqual(params["q"], "'; DROP TABLE nodes; --")
        self.assertEqual(params["limit"], 5)

    @patch("toto.ravioli.services.search._client")
    def test_connection_error_raises_unavailable(self, mock_client):
        from toto.ravioli.connection import Neo4jConnectionError
        from toto.ravioli.services.search import SearchUnavailableError, basic_search

        mock_client.return_value.run_cypher.side_effect = Neo4jConnectionError("down")
        with self.assertRaises(SearchUnavailableError):
            basic_search("foo")


# ---------------------------------------------------------------------------
# Service: advanced_search
# ---------------------------------------------------------------------------

class AdvancedSearchTests(TestCase):

    @patch("toto.ravioli.services.search._client")
    def test_queries_name_description_text(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []

        from toto.ravioli.services.search import advanced_search
        advanced_search("term")

        query_str = mock_client.return_value.run_cypher.call_args[0][0]
        self.assertIn("n.name", query_str)
        self.assertIn("n.description", query_str)
        self.assertIn("n.text", query_str)


# ---------------------------------------------------------------------------
# Service: deep_search
# ---------------------------------------------------------------------------

class DeepSearchTests(TestCase):

    @patch("toto.ravioli.services.search._client")
    def test_exact_wraps_in_quotes(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []

        from toto.ravioli.services.search import deep_search
        deep_search("hello world", exact=True)

        calls = mock_client.return_value.run_cypher.call_args_list
        # Second call is the actual search (first is ensure_fulltext_index)
        search_params = calls[-1][0][1]
        self.assertEqual(search_params["q"], '"hello world"')

    @patch("toto.ravioli.services.search._client")
    def test_non_exact_does_not_wrap(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []

        from toto.ravioli.services.search import deep_search
        deep_search("hello world", exact=False)

        calls = mock_client.return_value.run_cypher.call_args_list
        search_params = calls[-1][0][1]
        self.assertEqual(search_params["q"], "hello world")

    @patch("toto.ravioli.services.search._client")
    def test_score_included_in_result(self, mock_client):
        record = _make_record({"text": "chunk text"}, labels=["Chunk"], score=0.987)
        mock_client.return_value.run_cypher.return_value = [record]

        from toto.ravioli.services.search import deep_search
        results = deep_search("chunk")

        self.assertEqual(results[0]["score"], 0.987)


# ---------------------------------------------------------------------------
# View: search_view (direct mode)
# ---------------------------------------------------------------------------

class SearchViewDirectTests(TestCase):

    @patch("toto.ravioli.services.search._client")
    def test_empty_query_no_search_fired(self, mock_client):
        response = self.client.get("/ravioli/search/?exec=direct")
        self.assertEqual(response.status_code, 200)
        mock_client.assert_not_called()

    @patch("toto.ravioli.services.search._client")
    def test_direct_returns_results_in_context(self, mock_client):
        record = _make_record({"text": "hello"})
        mock_client.return_value.run_cypher.return_value = [record]

        response = self.client.get("/ravioli/search/?q=hello&exec=direct&mode=basic")
        self.assertEqual(response.status_code, 200)
        ctx = response.context
        self.assertTrue(ctx["searched"])
        self.assertEqual(len(ctx["results"]), 1)
        self.assertIsNone(ctx["error"])

    @patch("toto.ravioli.services.search._client")
    def test_neo4j_offline_shows_error_not_500(self, mock_client):
        from toto.ravioli.connection import Neo4jConnectionError
        mock_client.return_value.run_cypher.side_effect = Neo4jConnectionError("down")

        response = self.client.get("/ravioli/search/?q=test&exec=direct")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["error"])

    def test_invalid_limit_clamped(self):
        response = self.client.get("/ravioli/search/?q=test&exec=direct&limit=9999")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["limit"], 200)


# ---------------------------------------------------------------------------
# View: search_status_view
# ---------------------------------------------------------------------------

class SearchStatusViewTests(TestCase):

    def test_missing_run_id_returns_404(self):
        response = self.client.get("/ravioli/search/status/nonexistent-id/")
        self.assertEqual(response.status_code, 404)

    def test_done_returns_results(self):
        from django.core.cache import cache
        run_id = "test-run-123"
        results = [{"labels": ["Chunk"], "props": {"text": "hi"}}]
        cache.set(f"search:{run_id}:status", "done")
        cache.set(f"search:{run_id}:results", results)

        response = self.client.get(f"/ravioli/search/status/{run_id}/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "done")
        self.assertEqual(len(data["results"]), 1)

    def test_error_returns_error_message(self):
        from django.core.cache import cache
        run_id = "test-run-error"
        cache.set(f"search:{run_id}:status", "error")
        cache.set(f"search:{run_id}:error", "Neo4j is down")

        response = self.client.get(f"/ravioli/search/status/{run_id}/")
        data = json.loads(response.content)
        self.assertEqual(data["status"], "error")
        self.assertIn("Neo4j", data["error"])
