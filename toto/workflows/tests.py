"""
Workflow DAG tests.

KernelClient is mocked via settings.WORKFLOW_KERNEL_CLIENT — the executor
resolves this setting at call time so no real kernel process is needed.
"""

import json
from unittest.mock import MagicMock

from celery.exceptions import SoftTimeLimitExceeded
from django.test import TestCase, override_settings

from .models import (
    HumanTask,
    LambdaFunction,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .services.executor import WorkflowExecutor
from .services.human_task import apply_output_mapping, submit_human_task
from .services.validator import ValidationError, WorkflowValidator
from .output import WorkflowOutput, normalize_workflow_output
from .tasks import execute_lambda_node_task


def _make_kernel_mock(stdout_payload: dict):
    """Return a mock KernelClient class whose execute() returns a fixed payload."""
    mock_instance = MagicMock()
    mock_instance.execute.return_value = {"stdout": json.dumps(stdout_payload)}

    class MockKernelClient:
        def __new__(cls, *args, **kwargs):
            return mock_instance

    return MockKernelClient


def _lambda(name="fn") -> LambdaFunction:
    return LambdaFunction.objects.create(function_name=name, content="pass")


def _node(workflow, node_type, label="", lambda_fn=None, config=None) -> WorkflowNode:
    return WorkflowNode.objects.create(
        workflow=workflow,
        node_type=node_type,
        label=label,
        lambda_function=lambda_fn,
        config=config or {},
    )


def _edge(workflow, source, target, branch_key="", is_default=False) -> WorkflowEdge:
    return WorkflowEdge.objects.create(
        workflow=workflow,
        source=source,
        target=target,
        branch_key=branch_key,
        is_default=is_default,
    )


# ---------------------------------------------------------------------------
#  normalize_workflow_output
# ---------------------------------------------------------------------------

class NormalizeOutputTests(TestCase):

    def test_route_string(self):
        wo = normalize_workflow_output({"data": {"x": 1}, "route": "yes"})
        self.assertEqual(wo.routes, ["yes"])
        self.assertEqual(wo.data, {"x": 1})

    def test_routes_list(self):
        wo = normalize_workflow_output({"data": {}, "routes": ["a", "b"]})
        self.assertEqual(wo.routes, ["a", "b"])

    def test_no_route(self):
        wo = normalize_workflow_output({"data": {"k": "v"}})
        self.assertEqual(wo.routes, [])

    def test_non_dict_input(self):
        wo = normalize_workflow_output("garbage")
        self.assertIsInstance(wo, WorkflowOutput)
        self.assertEqual(wo.routes, [])

    def test_empty_dict(self):
        wo = normalize_workflow_output({})
        self.assertEqual(wo.data, {})
        self.assertEqual(wo.routes, [])


# ---------------------------------------------------------------------------
#  WorkflowValidator
# ---------------------------------------------------------------------------

class ValidatorTests(TestCase):

    def _workflow(self, name="W"):
        return Workflow.objects.create(name=name)

    def test_empty_workflow_fails(self):
        wf = self._workflow()
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("no nodes" in e for e in ctx.exception.errors))

    def test_lambda_without_function_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.LAMBDA, label="bad")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("no lambda_function" in e for e in ctx.exception.errors))

    def test_human_without_schema_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.HUMAN, label="h", config={})
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("config['schema']" in e for e in ctx.exception.errors))

    def test_split_without_single_incoming_fails(self):
        wf = self._workflow()
        fn = _lambda("f1")
        s = _node(wf, WorkflowNode.SPLIT, label="S")
        n1 = _node(wf, WorkflowNode.LAMBDA, label="N1", lambda_fn=fn)
        n2 = _node(wf, WorkflowNode.LAMBDA, label="N2", lambda_fn=_lambda("f2"))
        _edge(wf, n1, s)
        _edge(wf, n2, s)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("exactly one incoming" in e for e in ctx.exception.errors))

    def test_cycle_detected(self):
        wf = self._workflow()
        fn = _lambda("loop")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=_lambda("loop2"))
        _edge(wf, a, b)
        _edge(wf, b, a)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("cycle" in e for e in ctx.exception.errors))

    def test_valid_linear_passes(self):
        wf = self._workflow()
        fn1, fn2 = _lambda("a"), _lambda("b")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn1)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=fn2)
        _edge(wf, a, b)
        WorkflowValidator().validate(wf)

    def test_self_loop_fails(self):
        wf = self._workflow()
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=_lambda("sl"))
        _edge(wf, a, a)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("self-loop" in e for e in ctx.exception.errors))


# ---------------------------------------------------------------------------
#  Linear lambda workflow
# ---------------------------------------------------------------------------

class LinearLambdaWorkflowTests(TestCase):

    def _run_workflow(self, mock_output: dict) -> WorkflowRun:
        MockClient = _make_kernel_mock(mock_output)
        wf = Workflow.objects.create(name="Linear")
        fn1, fn2 = _lambda("step1"), _lambda("step2")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn1)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=fn2)
        _edge(wf, a, b)

        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 1})
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        return run

    def test_both_nodes_complete(self):
        run = self._run_workflow({"data": {"ok": True}, "route": "next"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(run.node_runs.count(), 2)
        self.assertTrue(run.node_runs.filter(status=WorkflowNodeRun.COMPLETED).count() == 2)

    def test_output_propagates(self):
        run = self._run_workflow({"data": {"msg": "hello"}, "route": "next"})
        b_run = run.node_runs.filter(node__label="B").first()
        self.assertIsNotNone(b_run)
        self.assertEqual(b_run.input_data.get("data", {}).get("msg"), "hello")

    def test_kernel_error_fails_run(self):
        instance = MagicMock()
        instance.execute.return_value = {"error": "kernel_crash"}

        wf = Workflow.objects.create(name="Err")
        fn = _lambda("err_fn")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn)
        run = WorkflowRun.objects.create(workflow=wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=type("MC", (), {"__new__": lambda cls, *a, **k: instance})):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.FAILED)


# ---------------------------------------------------------------------------
#  Celery lambda node execution
# ---------------------------------------------------------------------------

class CeleryLambdaWorkflowTests(TestCase):

    def test_async_lambdas_queue_one_task_per_lambda_node(self):
        MockClient = _make_kernel_mock({"data": {"ok": True}, "route": "next"})
        wf = Workflow.objects.create(name="CeleryLinear")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=_lambda("celery_a"))
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=_lambda("celery_b"))
        _edge(wf, a, b)

        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 1})
        with override_settings(
            WORKFLOW_KERNEL_CLIENT=MockClient,
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
        ):
            WorkflowExecutor(async_lambdas=True).start(run)

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(run.node_runs.filter(node__node_type=WorkflowNode.LAMBDA).count(), 2)
        self.assertEqual(run.node_runs.exclude(celery_task_id="").count(), 2)

    def test_lambda_task_failure_marks_node_and_run_failed(self):
        instance = MagicMock()
        instance.execute.return_value = {"error": "kernel_crash"}
        MockClient = type("MC", (), {"__new__": lambda cls, *a, **k: instance})

        wf = Workflow.objects.create(name="CeleryFailure")
        node = _node(wf, WorkflowNode.LAMBDA, label="bad", lambda_fn=_lambda("celery_fail"))
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.RUNNING)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=WorkflowNodeRun.PENDING,
            input_data={},
        )

        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            with self.assertRaises(RuntimeError):
                execute_lambda_node_task.apply(args=[node_run.id], throw=True)

        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertIn("kernel_crash", node_run.error)
        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_lambda_task_timeout_marks_node_and_run_failed(self):
        instance = MagicMock()
        instance.execute.side_effect = SoftTimeLimitExceeded()
        MockClient = type("MC", (), {"__new__": lambda cls, *a, **k: instance})

        wf = Workflow.objects.create(name="CeleryTimeout")
        node = _node(wf, WorkflowNode.LAMBDA, label="slow", lambda_fn=_lambda("celery_timeout"))
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.RUNNING)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=WorkflowNodeRun.PENDING,
            input_data={},
        )

        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            with self.assertRaises(SoftTimeLimitExceeded):
                execute_lambda_node_task.apply(args=[node_run.id], throw=True)

        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertIn("timed out", node_run.error)
        self.assertEqual(run.status, WorkflowRun.FAILED)


# ---------------------------------------------------------------------------
#  Split / Join workflow
# ---------------------------------------------------------------------------

class SplitJoinWorkflowTests(TestCase):

    def setUp(self):
        self.wf = Workflow.objects.create(name="SplitJoin")
        self.fn_start = _lambda("start")
        self.fn_a = _lambda("branch_a")
        self.fn_b = _lambda("branch_b")
        self.fn_end = _lambda("end")

        self.n_start = _node(self.wf, WorkflowNode.LAMBDA, label="start", lambda_fn=self.fn_start)
        self.n_split = _node(self.wf, WorkflowNode.SPLIT, label="split")
        self.n_a = _node(self.wf, WorkflowNode.LAMBDA, label="branch_a", lambda_fn=self.fn_a)
        self.n_b = _node(self.wf, WorkflowNode.LAMBDA, label="branch_b", lambda_fn=self.fn_b)
        self.n_join = _node(self.wf, WorkflowNode.JOIN, label="join")
        self.n_end = _node(self.wf, WorkflowNode.LAMBDA, label="end", lambda_fn=self.fn_end)

        _edge(self.wf, self.n_start, self.n_split)
        self.e_a = _edge(self.wf, self.n_split, self.n_a, branch_key="path_a")
        self.e_b = _edge(self.wf, self.n_split, self.n_b, branch_key="path_b")
        _edge(self.wf, self.n_a, self.n_join)
        _edge(self.wf, self.n_b, self.n_join)
        _edge(self.wf, self.n_join, self.n_end)

    def _run(self, start_output: dict, branch_output: dict | None = None):
        branch_output = branch_output or {"data": {}, "routes": []}

        class SeqMockClient:
            def __init__(self):
                pass

            def execute(self, fn_id, code):
                if fn_id == SeqMockClient.fn_start_id:
                    return {"stdout": json.dumps(start_output)}
                return {"stdout": json.dumps(branch_output)}

        SeqMockClient.fn_start_id = self.fn_start.id

        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=SeqMockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        return run

    def test_both_routes_activated(self):
        run = self._run({"data": {}, "routes": ["path_a", "path_b"]})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        activated = WorkflowEdgeRun.objects.filter(workflow_run=run, activated=True)
        self.assertTrue(activated.filter(edge__branch_key="path_a").exists())
        self.assertTrue(activated.filter(edge__branch_key="path_b").exists())

    def test_single_route_skips_other_branch(self):
        run = self._run({"data": {}, "route": "path_a"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        era = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_a)
        erb = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_b)
        self.assertTrue(era.activated)
        self.assertFalse(erb.activated)
        self.assertFalse(run.node_runs.filter(node=self.n_b).exists())

    def test_default_edge_fires_when_no_match(self):
        self.e_b.branch_key = ""
        self.e_b.is_default = True
        self.e_b.save()

        run = self._run({"data": {}, "route": "no_match"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        erb = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_b)
        self.assertTrue(erb.activated)


# ---------------------------------------------------------------------------
#  Human task
# ---------------------------------------------------------------------------

HUMAN_CONFIG = {
    "schema": {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "title": "Approve?"},
            "comment": {"type": "string", "title": "Comment"},
            "score_a": {"type": "number"},
            "score_b": {"type": "number"},
        },
        "required": ["approved"],
    },
    "output_mapping": {
        "route": {"field": "approved", "map": {"True": "approved_path", "False": "rejected_path"}},
        "data": {
            "comment": {"field": "comment"},
            "final_score": {"linear_combination": [
                {"field": "score_a", "weight": 0.6},
                {"field": "score_b", "weight": 0.4},
            ]},
        },
    },
}


class ApplyOutputMappingTests(TestCase):

    def test_route_via_field_map(self):
        result = apply_output_mapping({"approved": True, "comment": "looks good"}, HUMAN_CONFIG)
        self.assertEqual(result["routes"], ["approved_path"])
        self.assertEqual(result["data"]["comment"], "looks good")

    def test_route_rejected(self):
        result = apply_output_mapping({"approved": False}, HUMAN_CONFIG)
        self.assertEqual(result["routes"], ["rejected_path"])

    def test_linear_combination(self):
        result = apply_output_mapping({"approved": True, "score_a": 10, "score_b": 5}, HUMAN_CONFIG)
        self.assertAlmostEqual(result["data"]["final_score"], 8.0)

    def test_static_route(self):
        cfg = {"output_mapping": {"route": "always_this"}}
        result = apply_output_mapping({}, cfg)
        self.assertEqual(result["routes"], ["always_this"])

    def test_direct_field_route(self):
        cfg = {"output_mapping": {"route": {"field": "action"}}}
        result = apply_output_mapping({"action": "go_left"}, cfg)
        self.assertEqual(result["routes"], ["go_left"])

    def test_no_mapping_returns_empty(self):
        result = apply_output_mapping({"x": 1}, {})
        self.assertEqual(result["data"], {})
        self.assertEqual(result["routes"], [])


class HumanTaskWorkflowTests(TestCase):

    def setUp(self):
        self.wf = Workflow.objects.create(name="HumanWF")
        self.fn_start = _lambda("hs_start")
        self.fn_end = _lambda("hs_end")
        self.n_start = _node(self.wf, WorkflowNode.LAMBDA, label="start", lambda_fn=self.fn_start)
        self.n_human = _node(self.wf, WorkflowNode.HUMAN, label="human", config=HUMAN_CONFIG)
        self.n_end = _node(self.wf, WorkflowNode.LAMBDA, label="end", lambda_fn=self.fn_end)
        _edge(self.wf, self.n_start, self.n_human)
        self.e_approved = _edge(self.wf, self.n_human, self.n_end, branch_key="approved_path")

    def test_run_pauses_on_human(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.PAUSED)
        human_nr = run.node_runs.get(node=self.n_human)
        self.assertEqual(human_nr.status, WorkflowNodeRun.WAITING)
        self.assertTrue(HumanTask.objects.filter(node_run=human_nr).exists())

    def test_submit_resumes_and_completes(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()

        task = HumanTask.objects.get(node_run__workflow_run=run)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            submit_human_task(task, {"approved": True, "comment": "ok", "score_a": 8, "score_b": 6})

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)

        task.refresh_from_db()
        self.assertEqual(task.status, HumanTask.SUBMITTED)

        edge_run = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_approved)
        self.assertTrue(edge_run.activated)

    def test_submit_idempotent(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)

        task = HumanTask.objects.get(node_run__workflow_run=run)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            submit_human_task(task, {"approved": True})
            submit_human_task(task, {"approved": True})

        self.assertEqual(HumanTask.objects.filter(node_run__workflow_run=run).count(), 1)
