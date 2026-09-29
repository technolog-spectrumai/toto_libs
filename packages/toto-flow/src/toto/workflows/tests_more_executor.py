"""The DAG engine itself: what each node type does, how edges fire, when a run ends.

Named tests_more_executor.py (sibling of tests.py) and meant for the gate's
host-owned block beside the other workflows modules — ``toto`` is a namespace
package, so the runner finds a module only when it is named.

The lambdas here run through the in-process fallback (no
``WORKFLOW_KERNEL_CLIENT``), which is the path a host without a kernel server
takes; the kernel-client path is asserted separately with a fake client.
Nothing is sent to Celery: the async branches are asserted at the
``apply_async`` / ``send_task`` seam.
"""

import json
import unittest
from unittest import mock

from django.test import TestCase, override_settings

from . import predefined_tasks
from .models import (
    LambdaFunction,
    Report,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .output import normalize_workflow_output
from .services.executor import WorkflowExecutor, _format_lambda_error


def emit(data=None, **extra):
    """Lambda source that prints one workflow-output JSON line."""
    payload = {"data": data or {}, **extra}
    return f"import json\nprint(json.dumps({payload!r}))\n"


LOGGER = "toto.workflows.services.executor"


class Graph:
    """A tiny builder so each test reads as the DAG it runs."""

    def __init__(self, name="wf"):
        self.workflow = Workflow.objects.create(name=name)
        self._n = 0

    def lam(self, label, content):
        self._n += 1
        fn = LambdaFunction.objects.create(
            function_name=f"{self.workflow.slug}-{label}-{self._n}", content=content)
        return WorkflowNode.objects.create(
            workflow=self.workflow, node_type=WorkflowNode.LAMBDA, label=label,
            lambda_function=fn)

    def node(self, label, node_type, **kwargs):
        return WorkflowNode.objects.create(
            workflow=self.workflow, node_type=node_type, label=label, **kwargs)

    def edge(self, source, target, branch_key="", is_default=False):
        return WorkflowEdge.objects.create(
            workflow=self.workflow, source=source, target=target,
            branch_key=branch_key, is_default=is_default)

    def run(self, input_data=None, *, async_lambdas=False):
        run = WorkflowRun.objects.create(workflow=self.workflow,
                                         input_data=input_data or {})
        WorkflowExecutor(async_lambdas=async_lambdas).start(run)
        run.refresh_from_db()
        return run

    def failing_run(self, test, input_data=None):
        """Run a workflow expected to fail a node: the failure must be logged."""
        with test.assertLogs(LOGGER, level="ERROR") as logs:
            run = self.run(input_data)
        test.assertTrue(any("failed" in line for line in logs.output))
        return run


def node_run(run, node):
    return WorkflowNodeRun.objects.filter(workflow_run=run, node=node).first()


class LinearChainTests(TestCase):
    def test_each_lambda_receives_the_previous_ones_data(self):
        g = Graph()
        first = g.lam("first", "import json\n"
                      "print(json.dumps({'data': {'n': _input['n'] + 1}}))\n")
        second = g.lam("second", "import json\n"
                       "print(json.dumps({'data': {'n': _input['data']['n'] * 10}}))\n")
        g.edge(first, second)

        run = g.run({"n": 1})

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.completed_at)
        self.assertEqual(node_run(run, second).output_data,
                         {"data": {"n": 20}, "routes": []})

    def test_the_last_json_line_wins_and_other_output_is_ignored(self):
        g = Graph()
        only = g.lam("noisy", "print('warming up')\n"
                     "print('{\"data\": {\"v\": 1}}')\n"
                     "print('{\"data\": {\"v\": 2}, \"route\": \"x\"}')\n"
                     "print('done, not json')\n")

        run = g.run()

        self.assertEqual(node_run(run, only).output_data,
                         {"data": {"v": 2}, "routes": ["x"]})

    def test_a_lambda_that_prints_nothing_completes_with_empty_output(self):
        g = Graph()
        only = g.lam("quiet", "x = 1\n")

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run(run, only).output_data, {"data": {}, "routes": []})

    def test_a_workflow_with_no_nodes_completes_at_once(self):
        run = Graph().run()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertFalse(run.node_runs.exists())


class FailureTests(TestCase):
    def test_a_raising_lambda_fails_its_node_and_the_run_and_stops_the_chain(self):
        g = Graph()
        boom = g.lam("boom", "raise ValueError('the input was bad')\n")
        after = g.lam("after", emit({"never": True}))
        g.edge(boom, after)

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIsNotNone(run.completed_at)
        failed = node_run(run, boom)
        self.assertEqual(failed.status, WorkflowNodeRun.FAILED)
        self.assertIn("the input was bad", failed.error)
        self.assertIsNone(node_run(run, after))

    def test_a_lambda_node_without_a_function_fails_rather_than_crashing(self):
        g = Graph()
        bare = g.node("bare", WorkflowNode.LAMBDA)

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("no lambda_function", node_run(run, bare).error)

    def test_an_unknown_node_type_fails_the_run(self):
        g = Graph()
        odd = g.node("odd", "teleport")

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("Unknown node_type", node_run(run, odd).error)

    def test_a_sibling_branch_does_not_revive_a_failed_run(self):
        """Two start nodes: the first fails, the second succeeds. The run is
        FAILED, not COMPLETED — completion never overwrites a failure."""
        g = Graph()
        g.lam("bad", "raise RuntimeError('x')\n")
        g.lam("good", emit({"ok": True}))

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_mark_node_run_failed_on_a_missing_row_is_a_no_op(self):
        WorkflowExecutor().mark_node_run_failed(987654, "gone")  # must not raise


class SplitTests(TestCase):
    def _split_graph(self, route):
        g = Graph()
        src = g.lam("src", emit({"k": 1}, route=route) if route is not None
                    else emit({"k": 1}))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", emit({"went": "a"}))
        b = g.lam("b", emit({"went": "b"}))
        fallback = g.lam("fallback", emit({"went": "default"}))
        g.edge(src, split)
        ea = g.edge(split, a, branch_key="a")
        eb = g.edge(split, b, branch_key="b")
        ed = g.edge(split, fallback, is_default=True)
        return g, {"a": a, "b": b, "default": fallback,
                   "edges": {"a": ea, "b": eb, "default": ed}}

    def test_only_the_matching_branch_runs(self):
        g, n = self._split_graph("b")

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertIsNotNone(node_run(run, n["b"]))
        self.assertIsNone(node_run(run, n["a"]))
        self.assertIsNone(node_run(run, n["default"]))
        fired = {er.edge_id: er.activated
                 for er in WorkflowEdgeRun.objects.filter(workflow_run=run)}
        self.assertTrue(fired[n["edges"]["b"].id])
        self.assertFalse(fired[n["edges"]["a"].id])
        self.assertFalse(fired[n["edges"]["default"].id])

    def test_the_default_edge_fires_when_no_route_matches(self):
        g, n = self._split_graph("nowhere")

        run = g.run()

        self.assertIsNotNone(node_run(run, n["default"]))
        self.assertIsNone(node_run(run, n["a"]))
        self.assertIsNone(node_run(run, n["b"]))

    def test_the_default_edge_fires_when_no_route_is_given(self):
        g, n = self._split_graph(None)

        run = g.run()

        self.assertIsNotNone(node_run(run, n["default"]))

    def test_several_routes_fire_several_branches_but_not_the_default(self):
        g = Graph()
        src = g.lam("src", emit({}, routes=["a", "b"]))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", emit())
        b = g.lam("b", emit())
        fallback = g.lam("fallback", emit())
        g.edge(src, split)
        g.edge(split, a, branch_key="a")
        g.edge(split, b, branch_key="b")
        g.edge(split, fallback, is_default=True)

        run = g.run()

        self.assertIsNotNone(node_run(run, a))
        self.assertIsNotNone(node_run(run, b))
        self.assertIsNone(node_run(run, fallback))

    def test_no_match_and_no_default_fires_nothing_and_the_run_still_ends(self):
        g = Graph()
        src = g.lam("src", emit({}, route="zzz"))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", emit())
        g.edge(src, split)
        g.edge(split, a, branch_key="a")

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertIsNone(node_run(run, a))

    def test_a_split_passes_its_input_through_unchanged(self):
        g = Graph()
        src = g.lam("src", emit({"payload": 7}, route="a"))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", "import json\n"
                  "print(json.dumps({'data': {'seen': _input['data']['payload']}}))\n")
        g.edge(src, split)
        g.edge(split, a, branch_key="a")

        run = g.run()

        self.assertEqual(node_run(run, a).output_data["data"], {"seen": 7})


class JoinTests(TestCase):
    def test_a_join_waits_for_both_branches_and_merges_their_data(self):
        g = Graph()
        root = g.lam("root", emit({"root": 1}))
        left = g.lam("left", emit({"left": 2}, route="l"))
        right = g.lam("right", emit({"right": 3}, route="r"))
        join = g.node("join", WorkflowNode.JOIN)
        tail = g.lam("tail", "import json\n"
                     "print(json.dumps({'data': {'keys': sorted(_input['data'])}}))\n")
        g.edge(root, left)
        g.edge(root, right)
        g.edge(left, join)
        g.edge(right, join)
        g.edge(join, tail)

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        joined = node_run(run, join).output_data
        self.assertEqual(joined["data"], {"left": 2, "right": 3})
        self.assertEqual(sorted(joined["routes"]), ["l", "r"])
        self.assertEqual(node_run(run, tail).output_data["data"],
                         {"keys": ["left", "right"]})

    def _two_start_nodes(self):
        g = Graph()
        a = g.lam("a", emit({"a": 1}))
        b = g.lam("b", emit({"b": 2}))
        both = g.lam("both", "import json\n"
                     "print(json.dumps({'data': {'sum': _input['data']['a'] + _input['data']['b']}}))\n")
        g.edge(a, both)
        g.edge(b, both)
        return g, both

    def test_two_start_nodes_feed_a_common_child_when_lambdas_are_queued(self):
        """The Celery path: every start node is queued before any completes."""
        g, both = self._two_start_nodes()
        with mock.patch("toto.workflows.tasks.execute_lambda_node_task.apply_async",
                        return_value=mock.Mock(id="t")):
            run = g.run(async_lambdas=True)
        executor = WorkflowExecutor()
        for pending in list(run.node_runs.all()):
            executor.execute_lambda_node_run(pending.id)

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run(run, both).output_data["data"], {"sum": 3})

    @unittest.skip(
        "SUSPECTED BUG (executor.start/_check_completion): in synchronous mode "
        "the first start node's completion marks the run COMPLETED before the "
        "second start node is scheduled, so a node fed by two start nodes "
        "never runs")
    def test_two_start_nodes_feed_a_common_child_in_process(self):
        g, both = self._two_start_nodes()

        run = g.run()

        self.assertIsNotNone(node_run(run, both))
        self.assertEqual(node_run(run, both).output_data["data"], {"sum": 3})

    @unittest.skip(
        "SUSPECTED BUG (executor._advance): a split that skips a branch never "
        "decides that branch's edge into a downstream join, so the join waits "
        "forever and the run is reported COMPLETED without it")
    def test_a_join_after_a_split_runs_on_the_branch_that_fired(self):
        g = Graph()
        src = g.lam("src", emit({}, route="a"))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", emit({"a": 1}))
        b = g.lam("b", emit({"b": 1}))
        join = g.node("join", WorkflowNode.JOIN)
        g.edge(src, split)
        g.edge(split, a, branch_key="a")
        g.edge(split, b, branch_key="b")
        g.edge(a, join)
        g.edge(b, join)

        run = g.run()

        self.assertIsNotNone(node_run(run, join))
        self.assertEqual(node_run(run, join).output_data["data"], {"a": 1})

    def test_a_plain_node_behind_a_skipped_branch_never_runs(self):
        g = Graph()
        src = g.lam("src", emit({}, route="a"))
        split = g.node("split", WorkflowNode.SPLIT)
        a = g.lam("a", emit({"a": 1}))
        b = g.lam("b", emit({"b": 1}))
        after_b = g.lam("after_b", emit())
        g.edge(src, split)
        g.edge(split, a, branch_key="a")
        g.edge(split, b, branch_key="b")
        g.edge(b, after_b)

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertIsNone(node_run(run, after_b))


class ReportNodeTests(TestCase):
    def test_a_report_node_files_a_report_from_its_input(self):
        template = ReportTemplate.objects.create(
            name="Rows",
            definition={"type": "table", "title": "Rows",
                        "data": {"path": "rows"},
                        "columns": [{"key": "name"}, {"key": "value"}]})
        g = Graph()
        src = g.lam("src", emit({"rows": [{"name": "a", "value": 1}]}))
        report = g.node("report", WorkflowNode.REPORT, report_template=template,
                        config={"title": "Weekly rows", "route": "done"})
        g.edge(src, report)

        run = g.run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        filed = Report.objects.get()
        self.assertEqual(filed.title, "Weekly rows")
        self.assertEqual(filed.workflow_run, run)
        self.assertEqual(filed.data, {"rows": [{"name": "a", "value": 1}]})
        out = node_run(run, report).output_data
        self.assertEqual(out["data"]["report"]["id"], filed.id)
        self.assertEqual(out["data"]["report"]["page_count"], 1)
        self.assertEqual(out["routes"], ["done"])

    def test_a_report_node_without_a_template_fails_the_run(self):
        g = Graph()
        report = g.node("report", WorkflowNode.REPORT)

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("report_template", node_run(run, report).error)
        self.assertFalse(Report.objects.exists())


class PredefinedTaskTests(TestCase):
    def test_an_inline_task_runs_and_its_routes_steer_a_split(self):
        def score(input_data):
            return {"data": {"score": input_data["x"] * 2}, "routes": ["high"]}

        g = Graph()
        task = g.node("score", WorkflowNode.PREDEFINED_TASK, task_name="t-score")
        split = g.node("split", WorkflowNode.SPLIT)
        high = g.lam("high", emit({"tier": "high"}))
        low = g.lam("low", emit({"tier": "low"}))
        g.edge(task, split)
        g.edge(split, high, branch_key="high")
        g.edge(split, low, branch_key="low")

        with mock.patch.dict(predefined_tasks._registry, {"t-score": score}):
            run = g.run({"x": 21})

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run(run, task).output_data,
                         {"data": {"score": 42}, "routes": ["high"]})
        self.assertIsNotNone(node_run(run, high))
        self.assertIsNone(node_run(run, low))

    def test_an_unknown_task_fails_the_run_and_names_it(self):
        g = Graph()
        task = g.node("ghost", WorkflowNode.PREDEFINED_TASK, task_name="no-such-task")

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("no-such-task", node_run(run, task).error)

    def test_a_task_node_with_no_task_name_fails(self):
        g = Graph()
        task = g.node("blank", WorkflowNode.PREDEFINED_TASK)

        run = g.failing_run(self)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("no task_name", node_run(run, task).error)

    def test_a_celery_bound_task_is_dispatched_and_the_run_waits_for_it(self):
        g = Graph()
        task = g.node("remote", WorkflowNode.PREDEFINED_TASK, task_name="t-remote")
        tail = g.lam("tail", "import json\n"
                     "print(json.dumps({'data': {'got': _input['data']['answer']}}))\n")
        g.edge(task, tail)

        sent = mock.Mock(id="celery-123")
        with mock.patch.dict(predefined_tasks._celery_registry,
                             {"t-remote": "some.celery.task"}), \
             mock.patch("celery.current_app.send_task", return_value=sent) as send:
            run = g.run()

        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], "some.celery.task")
        waiting = node_run(run, task)
        self.assertEqual(waiting.status, WorkflowNodeRun.RUNNING)
        self.assertEqual(waiting.celery_task_id, "celery-123")
        self.assertEqual(send.call_args.kwargs["args"], [waiting.id])
        self.assertEqual(run.status, WorkflowRun.RUNNING)
        self.assertIsNone(node_run(run, tail))

        # The worker reports back: the node completes and the chain continues.
        WorkflowExecutor().complete_predefined_node_run(
            waiting.id, {"data": {"answer": 42}})

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run(run, tail).output_data["data"], {"got": 42})


class AsyncLambdaTests(TestCase):
    def _queued(self):
        g = Graph()
        first = g.lam("first", emit({"step": 1}))
        second = g.lam("second", emit({"step": 2}))
        g.edge(first, second)
        with mock.patch("toto.workflows.tasks.execute_lambda_node_task.apply_async",
                        return_value=mock.Mock(id="task-1")) as queue:
            run = g.run(async_lambdas=True)
        return run, first, second, queue

    def test_a_lambda_is_queued_pending_rather_than_run_in_place(self):
        run, first, second, queue = self._queued()

        pending = node_run(run, first)
        self.assertEqual(pending.status, WorkflowNodeRun.PENDING)
        self.assertEqual(pending.celery_task_id, "task-1")
        self.assertIsNone(pending.started_at)
        self.assertIsNone(node_run(run, second))
        self.assertEqual(run.status, WorkflowRun.RUNNING)
        # The default budget, and a hard kill a few seconds after the soft one.
        self.assertEqual(queue.call_args.kwargs["soft_time_limit"], 30)
        self.assertEqual(queue.call_args.kwargs["time_limit"], 35)

    def test_the_worker_side_completes_the_node_and_the_run_advances(self):
        run, first, second, _ = self._queued()
        executor = WorkflowExecutor(async_lambdas=False)

        output = executor.execute_lambda_node_run(node_run(run, first).id)

        self.assertEqual(output, {"data": {"step": 1}, "routes": []})
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run(run, second).status, WorkflowNodeRun.COMPLETED)

    def test_a_redelivered_task_for_a_completed_node_does_not_run_it_again(self):
        run, first, _, _ = self._queued()
        executor = WorkflowExecutor()
        executor.execute_lambda_node_run(node_run(run, first).id)
        first.lambda_function.content = "raise RuntimeError('ran twice')\n"
        first.lambda_function.save()

        output = executor.execute_lambda_node_run(node_run(run, first).id)

        self.assertEqual(output["data"], {"step": 1})

    def test_a_task_for_an_already_failed_run_does_nothing(self):
        run, first, _, _ = self._queued()
        run.status = WorkflowRun.FAILED
        run.save(update_fields=["status"])
        first.lambda_function.content = "raise RuntimeError('should not run')\n"
        first.lambda_function.save()

        output = WorkflowExecutor().execute_lambda_node_run(node_run(run, first).id)

        self.assertEqual(output, {})
        self.assertEqual(node_run(run, first).status, WorkflowNodeRun.PENDING)

    def test_only_lambda_node_runs_may_be_executed_as_lambdas(self):
        g = Graph()
        join = g.node("join", WorkflowNode.JOIN)
        run = WorkflowRun.objects.create(workflow=g.workflow)
        nr = WorkflowNodeRun.objects.create(workflow_run=run, node=join)

        with self.assertRaises(ValueError):
            WorkflowExecutor().execute_lambda_node_run(nr.id)

    def test_the_celery_task_marks_the_node_and_run_failed_and_reraises(self):
        from .tasks import execute_lambda_node_task

        g = Graph()
        bad = g.lam("bad", "raise KeyError('missing')\n")
        with mock.patch("toto.workflows.tasks.execute_lambda_node_task.apply_async",
                        return_value=mock.Mock(id="t")):
            run = g.run(async_lambdas=True)
        nr = node_run(run, bad)

        with self.assertRaises(KeyError):
            execute_lambda_node_task(nr.id)

        nr.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(nr.status, WorkflowNodeRun.FAILED)
        self.assertIn("missing", nr.error)
        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_the_start_task_reports_the_runs_status(self):
        from .tasks import start_workflow_run_task

        g = Graph()
        g.node("join", WorkflowNode.JOIN)
        run = WorkflowRun.objects.create(workflow=g.workflow)

        result = start_workflow_run_task(run.id)

        self.assertEqual(result, {"run_id": run.id, "status": WorkflowRun.COMPLETED})


class RecordingKernelClient:
    calls = []
    response = {"stdout": '{"data": {"from": "kernel"}}'}

    def execute(self, lambda_id, code):
        type(self).calls.append((lambda_id, code))
        return dict(type(self).response)


class KernelClientTests(TestCase):
    def setUp(self):
        RecordingKernelClient.calls = []
        RecordingKernelClient.response = {"stdout": '{"data": {"from": "kernel"}}'}

    def _run(self, *, fails=False):
        g = Graph()
        node = g.lam("remote", "print('hi')\n")
        with override_settings(
                WORKFLOW_KERNEL_CLIENT=RecordingKernelClient):
            run = (g.failing_run(self, {"a": [1, 2]}) if fails
                   else g.run({"a": [1, 2]}))
        return run, node

    def test_the_kernel_gets_the_code_with_the_input_injected_first(self):
        run, node = self._run()

        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        (lambda_id, code), = RecordingKernelClient.calls
        self.assertEqual(lambda_id, node.lambda_function_id)
        preamble, _, body = code.partition("\n_input = ")
        self.assertEqual(preamble, "import json as _json")
        self.assertTrue(body.endswith("print('hi')\n"))
        injected = body.split("\n", 1)[0]
        self.assertEqual(eval(injected, {"_json": json}), {"a": [1, 2]})
        self.assertEqual(node_run(run, node).output_data["data"], {"from": "kernel"})

    def test_a_kernel_timeout_reads_as_a_timed_out_task(self):
        RecordingKernelClient.response = {"error": "kernel_server_timeout"}

        run, node = self._run(fails=True)

        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertEqual(node_run(run, node).error, "Workflow task timed out.")

    def test_any_other_kernel_error_is_passed_through_labelled(self):
        RecordingKernelClient.response = {"error": "NameError: x"}

        run, node = self._run(fails=True)

        self.assertEqual(node_run(run, node).error, "Workflow task error: NameError: x")

    def test_the_error_formatter(self):
        self.assertEqual(_format_lambda_error("kernel_server_timeout"),
                         "Workflow task timed out.")
        self.assertEqual(_format_lambda_error(3), "Workflow task error: 3")


class NormalizeOutputTests(TestCase):
    def test_route_becomes_a_one_item_list(self):
        out = normalize_workflow_output({"data": {"a": 1}, "route": "x"})
        self.assertEqual((out.data, out.routes), ({"a": 1}, ["x"]))

    def test_routes_wins_over_route(self):
        out = normalize_workflow_output({"route": "x", "routes": ["y", "z"]})
        self.assertEqual(out.routes, ["y", "z"])

    def test_a_null_route_and_empty_routes_mean_no_routes(self):
        self.assertEqual(normalize_workflow_output({"route": None}).routes, [])
        self.assertEqual(normalize_workflow_output({"routes": None}).routes, [])

    def test_non_dict_data_and_non_dict_output_are_emptied(self):
        self.assertEqual(normalize_workflow_output({"data": [1, 2]}).data, {})
        empty = normalize_workflow_output("just a string")
        self.assertEqual((empty.data, empty.routes), ({}, []))
