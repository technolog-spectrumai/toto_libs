"""Tests for ravioli search service and views."""

import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

User = get_user_model()


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

    values = {
        "labels": list(labels),
        "n": node,
        "node": node,
        "score": score,
    }
    record = MagicMock()
    record.get = lambda key, default=None: values.get(key, default)
    # Without this, record["score"] hits MagicMock's default __getitem__ and
    # float(MagicMock) silently yields 1.0 instead of the configured score.
    record.__getitem__ = lambda self, key: values[key]
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

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(self.user)

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

    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        self.user = User.objects.create_superuser("root", "r@example.com", "pw")
        self.client.force_login(self.user)

    def _make_run(self, *, status, output=None, error=""):
        from toto.workflows.models import Workflow, WorkflowNodeRun, WorkflowRun

        wf = Workflow.objects.create(slug="ravioli-graph-search", name="s")
        run = WorkflowRun.objects.create(workflow=wf, status=status)
        node = wf.nodes.create(node_type="predefined_task", label="s", task_name="ravioli_graph_search")
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=node, status=WorkflowNodeRun.COMPLETED,
            output_data=output or {}, error=error,
        )
        return run

    def test_missing_run_id_returns_404(self):
        response = self.client.get(reverse("ravioli:search_status", args=[999999]))
        self.assertEqual(response.status_code, 404)

    def test_done_returns_results(self):
        from toto.workflows.models import WorkflowRun

        run = self._make_run(status=WorkflowRun.COMPLETED, output={"data": {
            "results": [{"labels": ["Chunk"], "props": {"text": "hi"}}],
            "requested_mode": "keyword", "effective_mode": "keyword",
        }})
        response = self.client.get(reverse("ravioli:search_status", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data["status"], "done")
        self.assertEqual(len(data["results"]), 1)

    def test_error_returns_error_message(self):
        from toto.workflows.models import WorkflowRun

        run = self._make_run(status=WorkflowRun.FAILED, error="Neo4j is down")
        response = self.client.get(reverse("ravioli:search_status", args=[run.pk]))
        data = json.loads(response.content)
        self.assertEqual(data["status"], "error")
        self.assertIn("Neo4j", data["error"])


# ---------------------------------------------------------------------------
# Security: ravioli/bento require login (no Cypher/graph access for anon users)
# ---------------------------------------------------------------------------

class AuthGateTests(TestCase):
    """Unauthenticated users must be redirected to login for every ravioli/bento
    endpoint that can run Cypher or touch the graph."""

    def test_ravioli_views_block_anonymous(self):
        for name, args in [
            ("ravioli:query_unified", []),
            ("ravioli:search", []),
            ("ravioli:query_graph_data", [1]),
            ("ravioli:query_cached_data", [1]),
            ("ravioli:search_status", [1]),
            ("ravioli:graph_analysis", []),
            ("ravioli:history", []),
        ]:
            res = self.client.get(reverse(name, args=args))
            self.assertEqual(res.status_code, 302, f"{name} must redirect anon")
            self.assertIn("next=", res.url, f"{name} must redirect to login")

    def test_run_cypher_blocks_anonymous(self):
        res = self.client.post(reverse("ravioli:run_cypher_query", args=[1]))
        self.assertEqual(res.status_code, 302)
        self.assertIn("next=", res.url)

    def test_bento_blocks_anonymous(self):
        res = self.client.get(reverse("bento:node_list"))
        self.assertEqual(res.status_code, 302, "bento node_list must redirect anon")
        self.assertIn("next=", res.url)
