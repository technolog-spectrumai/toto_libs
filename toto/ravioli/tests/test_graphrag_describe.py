"""Tests for the 'Ask Steven' GraphRAG tab (Celery task + workflow + views)."""
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

_RAVIOLI = apps.is_installed("toto.ravioli")
_SABBIA = apps.is_installed("toto.sabbia")


@override_settings(OPENAI_API_KEY="sk-x")
class DescribeTaskTests(TestCase):
    def _steven(self):
        from toto.sabbia.models import Agent

        return Agent.objects.create(
            name="Steven", slug="steven", endpoint_type=Agent.ENDPOINT_OPENAI,
            model_name="gpt-4.1-mini", is_active=True,
        )

    def test_happy_path_returns_answer(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        from toto.ravioli.predefined_tasks import ravioli_graphrag_describe

        self._steven()
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value={
                 "answer": "graph summary", "context": "ctx", "cause": "",
                 "graph": {"nodes": [{"id": "a", "label": "X"}], "edges": []},
             }) as rg:
            out = ravioli_graphrag_describe({"data": {"question": "describe it", "top_k": 5}})
        self.assertEqual(out["data"]["answer"], "graph summary")
        self.assertEqual(out["data"]["context"], "ctx")
        self.assertEqual(out["data"]["graph"]["nodes"][0]["id"], "a")  # graph passed through
        self.assertEqual(rg.call_args.kwargs["query_text"], "describe it")
        self.assertEqual(rg.call_args.kwargs["top_k"], 5)
        self.assertEqual(out["data"]["agent"], "Steven")  # openai provider → the openai agent

    def test_empty_question_uses_default_prompt(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        from toto.ravioli.predefined_tasks import GRAPHRAG_DESCRIBE_PROMPT, ravioli_graphrag_describe

        self._steven()
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value={
                 "answer": "x", "context": "", "cause": "", "graph": {"nodes": [], "edges": []},
             }) as rg:
            ravioli_graphrag_describe({"data": {}})
        self.assertEqual(rg.call_args.kwargs["query_text"], GRAPHRAG_DESCRIBE_PROMPT)

    def test_no_answer_raises_with_cause(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        from toto.ravioli.predefined_tasks import ravioli_graphrag_describe

        self._steven()
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value={
                 "answer": "", "context": "", "cause": "text2cypher: APIConnectionError",
                 "graph": {"nodes": [], "edges": []},
             }):
            with self.assertRaisesRegex(RuntimeError, "text2cypher: APIConnectionError"):
                ravioli_graphrag_describe({"data": {"question": "q"}})

    @override_settings(GRAPHRAG_PROVIDER="ollama")
    def test_provider_switch_selects_matching_agent(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        from toto.sabbia.models import Agent
        from toto.ravioli.predefined_tasks import ravioli_graphrag_describe

        self._steven()  # openai agent
        Agent.objects.create(
            name="Ollama Local", slug="ollama-local", endpoint_type=Agent.ENDPOINT_OLLAMA,
            model_name="qwen3:1.7b", is_active=True,
        )
        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True), \
             mock.patch("toto.sabbia.graphrag_llm.build_llm", return_value="LLM"), \
             mock.patch("toto.ravioli.rag.run_graphrag", return_value={
                 "answer": "ans", "context": "ctx", "cause": "", "graph": {"nodes": [], "edges": []},
             }):
            out = ravioli_graphrag_describe({"data": {"question": "q"}})
        self.assertEqual(out["data"]["agent"], "Ollama Local")  # ollama provider → ollama agent

    def test_no_agent_raises(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        from toto.ravioli.predefined_tasks import ravioli_graphrag_describe

        with mock.patch("toto.ravioli.connection.is_enabled", return_value=True):
            with self.assertRaises(RuntimeError):
                ravioli_graphrag_describe({"data": {"question": "q"}})


class DescribeViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_page_renders_with_cytoscape(self):
        res = self.client.get(reverse("ravioli:graphrag_describe"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "Ask AI")          # renamed tab/heading
        self.assertContains(res, "cytoscape")       # context-graph rendering wired

    @override_settings(RAVIOLI_ENABLED=True)
    def test_start_triggers_workflow(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        run = mock.Mock(pk=123)
        with mock.patch("toto.ravioli.views.celery_available", return_value=True), \
             mock.patch("toto.ravioli.views._trigger_workflow", return_value=run) as trig:
            res = self.client.post(reverse("ravioli:graphrag_describe_start"), {"question": "hi"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["run_id"], 123)
        self.assertEqual(trig.call_args.args[0], "ravioli-graphrag-describe")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_start_blocked_when_no_celery_worker(self):
        if not _SABBIA:
            self.skipTest("toto.sabbia not installed")
        with mock.patch("toto.ravioli.views.celery_available", return_value=False):
            res = self.client.post(reverse("ravioli:graphrag_describe_start"), {"question": "hi"})
        self.assertEqual(res.status_code, 503)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_start_blocked_when_graph_off(self):
        res = self.client.post(reverse("ravioli:graphrag_describe_start"), {"question": "hi"})
        self.assertEqual(res.status_code, 503)

    def test_status_done_returns_answer(self):
        from toto.workflows.models import Workflow, WorkflowNodeRun, WorkflowRun

        wf = Workflow.objects.create(slug="ravioli-graphrag-describe", name="gr")
        run = WorkflowRun.objects.create(workflow=wf, status="completed")
        node = wf.nodes.create(node_type="predefined_task", label="gr", task_name="ravioli_graphrag_describe")
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=node, status=WorkflowNodeRun.COMPLETED,
            output_data={"data": {"answer": "the graph has X and Y", "context": "c", "agent": "Steven"}},
        )
        res = self.client.get(reverse("ravioli:graphrag_describe_status", args=[run.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "done")
        self.assertEqual(body["answer"], "the graph has X and Y")
        self.assertEqual(body["agent"], "Steven")
