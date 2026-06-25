"""Tests for graph search modes: mode resolution, run_search, view, task, status endpoint."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import unittest

from django.apps import apps
from django.contrib.auth.models import AnonymousUser, User
from django.test import SimpleTestCase, TestCase, RequestFactory, override_settings

# View-level tests require toto.ravioli in INSTALLED_APPS (needs BUILD_NEO4J=1)
_RAVIOLI_INSTALLED = apps.is_installed("toto.ravioli")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_record(props, labels=("Node",), score=None):
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


def _fake_vec_results(n=1):
    return [
        {"labels": ["TotoChunk"], "props": {"text": f"chunk {i}"}, "score": 0.9 - i * 0.1}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# resolve_mode
# ---------------------------------------------------------------------------

class ResolveModeTests(SimpleTestCase):

    def test_keyword_is_default_for_unknown(self):
        from toto.ravioli.services.search import resolve_mode, MODE_KEYWORD
        self.assertEqual(resolve_mode("nonsense"), MODE_KEYWORD)
        self.assertEqual(resolve_mode(""), MODE_KEYWORD)
        self.assertEqual(resolve_mode(None), MODE_KEYWORD)

    def test_canonical_modes_pass_through(self):
        from toto.ravioli.services.search import resolve_mode
        for m in ("keyword", "fulltext", "semantic"):
            self.assertEqual(resolve_mode(m), m)

    def test_legacy_aliases_accepted(self):
        from toto.ravioli.services.search import resolve_mode
        self.assertEqual(resolve_mode("basic"),    "basic")
        self.assertEqual(resolve_mode("advanced"), "advanced")
        self.assertEqual(resolve_mode("deep"),     "deep")

    def test_canonical_mode_maps_aliases_to_display(self):
        from toto.ravioli.services.search import canonical_mode
        self.assertEqual(canonical_mode("basic"),    "keyword")
        self.assertEqual(canonical_mode("advanced"), "keyword")
        self.assertEqual(canonical_mode("deep"),     "fulltext")
        self.assertEqual(canonical_mode("semantic"), "semantic")


# ---------------------------------------------------------------------------
# run_search — semantic mode
# ---------------------------------------------------------------------------

class RunSearchSemanticTests(SimpleTestCase):

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=True)
    def test_semantic_mode_explicit_succeeds(self):
        from toto.ravioli.services.search import run_search, MODE_SEMANTIC

        with patch(
            "toto.ravioli.services.search.semantic_available", return_value=True
        ), patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            return_value=_fake_vec_results(1),
        ):
            result = run_search("query", mode="semantic")

        self.assertEqual(result["effective_mode"], MODE_SEMANTIC)
        self.assertFalse(result["fallback_used"])


# ---------------------------------------------------------------------------
# run_search — semantic mode, fallback when vector unavailable
# ---------------------------------------------------------------------------

class RunSearchSemanticFallbackTests(SimpleTestCase):

    @override_settings(VICUNA_EMBEDDINGS_ENABLED=False)
    def test_semantic_falls_back_when_embeddings_disabled(self):
        from toto.ravioli.services.search import run_search, MODE_KEYWORD
        from toto.ravioli.vector_search import VectorSearchUnavailable

        with patch(
            "toto.ravioli.services.search.semantic_available", return_value=False
        ), patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            side_effect=VectorSearchUnavailable("embeddings disabled"),
        ), patch(
            "toto.ravioli.services.search.advanced_search",
            return_value=[{"labels": ["N"], "props": {"name": "x"}}],
        ) as mock_adv:
            result = run_search("hello", mode="semantic")

        self.assertEqual(result["effective_mode"], MODE_KEYWORD)
        self.assertTrue(result["fallback_used"])
        self.assertIn("embeddings disabled", result["fallback_reason"])
        mock_adv.assert_called_once()

    def test_semantic_falls_back_when_vector_query_fails(self):
        from toto.ravioli.services.search import run_search, MODE_KEYWORD
        from toto.ravioli.vector_search import VectorSearchUnavailable

        with patch(
            "toto.ravioli.services.search.semantic_available", return_value=True
        ), patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            side_effect=VectorSearchUnavailable("Neo4j down"),
        ), patch(
            "toto.ravioli.services.search.advanced_search", return_value=[],
        ):
            result = run_search("hello", mode="semantic")

        self.assertEqual(result["effective_mode"], MODE_KEYWORD)
        self.assertTrue(result["fallback_used"])
        self.assertIn("Neo4j down", result["fallback_reason"])

    def test_fallback_never_raises_http_500(self):
        """run_search with semantic mode must not propagate VectorSearchUnavailable."""
        from toto.ravioli.services.search import run_search
        from toto.ravioli.vector_search import VectorSearchUnavailable

        with patch(
            "toto.ravioli.services.search.semantic_available", return_value=True
        ), patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            side_effect=VectorSearchUnavailable("anything"),
        ), patch(
            "toto.ravioli.services.search.advanced_search", return_value=[],
        ):
            try:
                result = run_search("q", mode="semantic")
            except Exception as exc:
                self.fail(f"run_search raised unexpectedly: {exc}")

        self.assertIn("results", result)


# ---------------------------------------------------------------------------
# run_search — legacy modes still work
# ---------------------------------------------------------------------------

class RunSearchLegacyModesTests(SimpleTestCase):

    @patch("toto.ravioli.services.search._client")
    def test_basic_mode_calls_basic_search(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []
        from toto.ravioli.services.search import run_search
        result = run_search("q", mode="basic")
        self.assertEqual(result["effective_mode"], "keyword")

    @patch("toto.ravioli.services.search._client")
    def test_advanced_mode_calls_advanced_search(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []
        from toto.ravioli.services.search import run_search
        result = run_search("q", mode="advanced")
        self.assertEqual(result["effective_mode"], "keyword")

    @patch("toto.ravioli.services.search._client")
    def test_deep_mode_calls_deep_search(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []
        from toto.ravioli.services.search import run_search
        result = run_search("q", mode="deep")
        self.assertEqual(result["effective_mode"], "fulltext")

    @patch("toto.ravioli.services.search._client")
    def test_keyword_mode(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []
        from toto.ravioli.services.search import run_search
        result = run_search("q", mode="keyword")
        self.assertEqual(result["effective_mode"], "keyword")

    @patch("toto.ravioli.services.search._client")
    def test_fulltext_mode(self, mock_client):
        mock_client.return_value.run_cypher.return_value = []
        from toto.ravioli.services.search import run_search
        result = run_search("q", mode="fulltext")
        self.assertEqual(result["effective_mode"], "fulltext")


# ---------------------------------------------------------------------------
# run_search — result normalization
# ---------------------------------------------------------------------------

class ResultNormalizationTests(SimpleTestCase):

    @patch("toto.ravioli.services.search._client")
    def test_keyword_results_have_score_none(self, mock_client):
        record = _make_record({"text": "hello"})
        mock_client.return_value.run_cypher.return_value = [record]
        from toto.ravioli.services.search import run_search
        result = run_search("hello", mode="keyword")
        self.assertIn("score", result["results"][0])
        self.assertIsNone(result["results"][0]["score"])

    def test_semantic_results_keep_score(self):
        from toto.ravioli.services.search import run_search

        with patch(
            "toto.ravioli.services.search.semantic_available", return_value=True
        ), patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            return_value=[{"labels": ["C"], "props": {"text": "hi"}, "score": 0.85}],
        ):
            result = run_search("hi", mode="semantic")

        self.assertEqual(result["results"][0]["score"], 0.85)

    @patch("toto.ravioli.services.search._client")
    def test_results_always_have_labels_props_score(self, mock_client):
        record = _make_record({"name": "foo"}, labels=("Widget",))
        mock_client.return_value.run_cypher.return_value = [record]
        from toto.ravioli.services.search import run_search
        result = run_search("foo", mode="basic")
        r = result["results"][0]
        self.assertIn("labels", r)
        self.assertIn("props", r)
        self.assertIn("score", r)


# ---------------------------------------------------------------------------
# predefined_tasks.ravioli_graph_search
# ---------------------------------------------------------------------------

class PredefinedTaskTests(SimpleTestCase):

    def test_default_mode_is_keyword(self):
        # When mode is absent from input_data, resolve_mode returns "keyword"
        with patch("toto.ravioli.services.search.run_search", return_value={
            "results": [],
            "requested_mode": "keyword",
            "effective_mode": "keyword",
            "fallback_used": False,
            "fallback_reason": None,
            "semantic_available": False,
        }) as mock_rs:
            from toto.ravioli.predefined_tasks import ravioli_graph_search
            ravioli_graph_search({"data": {"q": "hello"}})
        # run_search should be called with mode="keyword" (the default)
        self.assertEqual(mock_rs.call_args[1]["mode"], "keyword")

    def test_output_includes_metadata(self):
        with patch("toto.ravioli.services.search.run_search", return_value={
            "results": [],
            "requested_mode": "keyword",
            "effective_mode": "keyword",
            "fallback_used": True,
            "fallback_reason": "disabled",
            "semantic_available": False,
        }):
            from toto.ravioli.predefined_tasks import ravioli_graph_search
            out = ravioli_graph_search({"data": {"q": "x"}})

        data = out["data"]
        self.assertIn("requested_mode", data)
        self.assertIn("effective_mode", data)
        self.assertIn("fallback_used", data)
        self.assertIn("fallback_reason", data)
        self.assertIn("semantic_available", data)
        self.assertTrue(data["fallback_used"])

    def test_legacy_mode_basic_accepted(self):
        with patch("toto.ravioli.services.search.run_search", return_value={
            "results": [], "requested_mode": "keyword", "effective_mode": "keyword",
            "fallback_used": False, "fallback_reason": None, "semantic_available": False,
        }) as mock_rs:
            from toto.ravioli.predefined_tasks import ravioli_graph_search
            ravioli_graph_search({"data": {"q": "y", "mode": "basic"}})

        self.assertEqual(mock_rs.call_args[1]["mode"], "basic")


# ---------------------------------------------------------------------------
# search_status_view — metadata in response
# ---------------------------------------------------------------------------

@unittest.skipUnless(_RAVIOLI_INSTALLED, "requires BUILD_NEO4J=1")
class SearchStatusMetadataTests(TestCase):

    def _make_workflow_run(self, status, output_data):
        from toto.workflows.models import WorkflowNodeRun, WorkflowRun, Workflow
        wf = Workflow.objects.create(slug="ravioli-graph-search", name="search")
        run = WorkflowRun.objects.create(workflow=wf, status=status)
        if output_data is not None:
            node = wf.nodes.create(
                node_type="predefined_task",
                label="search",
                task_name="ravioli_graph_search",
            )
            WorkflowNodeRun.objects.create(
                workflow_run=run,
                node=node,
                status=WorkflowNodeRun.COMPLETED,
                output_data=output_data,
            )
        return run

    def _call_status_view(self, run_id):
        from toto.ravioli.views import search_status_view
        rf = RequestFactory()
        request = rf.get(f"/ravioli/search/status/{run_id}/")
        # search_status_view now requires login.
        request.user = SimpleNamespace(is_authenticated=True, is_active=True, is_staff=True)
        return search_status_view(request, run_id=run_id)

    def test_status_done_includes_metadata(self):
        run = self._make_workflow_run(
            status="completed",
            output_data={
                "data": {
                    "results": [{"labels": ["N"], "props": {}, "score": None}],
                    "requested_mode": "semantic",
                    "effective_mode": "semantic",
                    "fallback_used": False,
                    "fallback_reason": None,
                    "semantic_available": True,
                }
            },
        )
        resp = self._call_status_view(run.pk)
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["status"], "done")
        self.assertEqual(data["effective_mode"], "semantic")
        self.assertFalse(data["fallback_used"])
        self.assertTrue(data["semantic_available"])

    def test_status_done_fallback_reflected(self):
        run = self._make_workflow_run(
            status="completed",
            output_data={
                "data": {
                    "results": [],
                    "requested_mode": "keyword",
                    "effective_mode": "keyword",
                    "fallback_used": True,
                    "fallback_reason": "embeddings disabled",
                    "semantic_available": False,
                }
            },
        )
        resp = self._call_status_view(run.pk)
        data = json.loads(resp.content)
        self.assertEqual(data["effective_mode"], "keyword")
        self.assertTrue(data["fallback_used"])
        self.assertEqual(data["fallback_reason"], "embeddings disabled")


# ---------------------------------------------------------------------------
# search_view context — metadata and exec controls
# ---------------------------------------------------------------------------

@unittest.skipUnless(_RAVIOLI_INSTALLED, "requires BUILD_NEO4J=1")
class SearchViewContextTests(SimpleTestCase):
    """Test search view behavior by mocking PageProcessor and render.

    Uses RequestFactory + mocking so no DB or URLconf is needed.
    """

    def _call_view(self, qs, user=None, **settings_kwargs):
        """Call search_view, mock PageProcessor+render, return captured context dict."""
        from toto.ravioli.views import search_view
        rf = RequestFactory()
        request = rf.get(f"/ravioli/search/?{qs}")
        # search_view now requires login; default to an authenticated non-staff user.
        request.user = user or SimpleNamespace(
            is_authenticated=True, is_active=True, is_staff=False, is_superuser=False
        )
        captured = {}

        def fake_decorate(ctx, req):
            captured["ctx"] = dict(ctx)
            return ctx

        with patch("toto.ravioli.views.PageProcessor") as MockPP, \
             patch("toto.ravioli.views.render", return_value=MagicMock(status_code=200)):
            MockPP.return_value.decorate.side_effect = fake_decorate
            if settings_kwargs:
                with self.settings(**settings_kwargs):
                    search_view(request)
            else:
                search_view(request)
        return captured.get("ctx", {})

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_default_mode_is_keyword(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hello&exec=direct")
        self.assertEqual(ctx["mode"], "keyword")

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_semantic_choice_hidden_when_unavailable(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hi&exec=direct")
        modes = [c[0] for c in ctx["search_method_choices"]]
        self.assertNotIn("semantic", modes)
        self.assertNotIn("auto", modes)   # auto mode is gone entirely

    @patch("toto.ravioli.services.search.semantic_available", return_value=True)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_semantic_choice_shown_when_available(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hi&exec=direct")
        modes = [c[0] for c in ctx["search_method_choices"]]
        self.assertIn("semantic", modes)
        self.assertNotIn("auto", modes)

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_stale_semantic_url_resets_to_keyword(self, mock_adv, mock_sem):
        # A bookmarked ?mode=semantic must not stick once embeddings are gone.
        ctx = self._call_view("q=hi&exec=direct&mode=semantic")
        self.assertEqual(ctx["mode"], "keyword")

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_context_contains_search_meta(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hello&exec=direct")
        self.assertIn("search_meta", ctx)
        meta = ctx["search_meta"]
        self.assertIn("effective_mode", meta)
        self.assertIn("fallback_used", meta)
        self.assertIn("semantic_available", meta)

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_non_staff_exec_controls_hidden(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hello&exec=direct")
        self.assertFalse(ctx["show_exec_controls"])

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_staff_sees_exec_controls(self, mock_adv, mock_sem):
        staff = SimpleNamespace(is_authenticated=True, is_staff=True)
        ctx = self._call_view("q=hello&exec=direct", user=staff)
        self.assertTrue(ctx["show_exec_controls"])

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_debug_mode_shows_exec_controls(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hello&exec=direct", DEBUG=True)
        self.assertTrue(ctx["show_exec_controls"])

    @patch("toto.ravioli.services.search.semantic_available", return_value=True)
    def test_semantic_success_reflected_in_context(self, mock_sem):
        with patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            return_value=_fake_vec_results(1),
        ):
            ctx = self._call_view("q=hello&exec=direct&mode=semantic")
        meta = ctx["search_meta"]
        self.assertEqual(meta["effective_mode"], "semantic")
        self.assertFalse(meta["fallback_used"])

    @patch("toto.ravioli.services.search.semantic_available", return_value=True)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_semantic_fallback_reflected_in_context(self, mock_adv, mock_sem):
        from toto.ravioli.vector_search import VectorSearchUnavailable
        with patch(
            "toto.ravioli.vector_search.retrieve_vector_results",
            side_effect=VectorSearchUnavailable("index missing"),
        ):
            ctx = self._call_view("q=hello&exec=direct&mode=semantic")
        meta = ctx["search_meta"]
        self.assertEqual(meta["effective_mode"], "keyword")
        self.assertTrue(meta["fallback_used"])
        self.assertIn("index missing", meta["fallback_reason"])

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search", return_value=[])
    def test_invalid_mode_falls_back_to_keyword(self, mock_adv, mock_sem):
        ctx = self._call_view("q=hello&exec=direct&mode=GARBAGE")
        self.assertEqual(ctx["mode"], "keyword")

    @patch("toto.ravioli.services.search.semantic_available", return_value=False)
    @patch("toto.ravioli.services.search.advanced_search")
    def test_neo4j_offline_direct_returns_200_not_500(self, mock_adv, mock_sem):
        from toto.ravioli.services.search import SearchUnavailableError
        mock_adv.side_effect = SearchUnavailableError("Neo4j is down")
        # Should not raise; error is captured in context
        ctx = self._call_view("q=down&exec=direct")
        self.assertIsNotNone(ctx.get("error"))
