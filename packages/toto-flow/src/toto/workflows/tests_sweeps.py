"""Workflows' stuck-run closers and their sweep policies."""

import datetime

from django.test import TestCase
from django.utils import timezone

from toto.quota import sweeps

from .models import Workflow, WorkflowNode, WorkflowNodeRun, WorkflowRun
from .services.executor import fail_stuck_node_run, fail_stuck_workflow_run


def make_run(*, hours_ago, status=WorkflowRun.RUNNING):
    workflow = Workflow.objects.create(name=f"wf-{hours_ago}-{status}",
                                       slug=f"wf-{hours_ago}-{status}")
    return WorkflowRun.objects.create(
        workflow=workflow, status=status,
        started_at=timezone.now() - datetime.timedelta(hours=hours_ago),
    )


def make_node_run(run, *, hours_ago, status=WorkflowNodeRun.RUNNING):
    node = WorkflowNode.objects.create(
        workflow=run.workflow, label=f"n-{run.pk}", node_type=WorkflowNode.JOIN)
    return WorkflowNodeRun.objects.create(
        workflow_run=run, node=node, status=status,
        started_at=timezone.now() - datetime.timedelta(hours=hours_ago),
    )


class CloserTests(TestCase):
    def test_node_closer_cascades_to_the_run_with_completed_at(self):
        run = make_run(hours_ago=4)
        node_run = make_node_run(run, hours_ago=4)

        fail_stuck_node_run(node_run, "swept")

        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertEqual(node_run.error, "swept")
        self.assertIsNotNone(node_run.completed_at)
        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIsNotNone(run.completed_at)

    def test_run_closer_sets_status_and_completed_at(self):
        run = make_run(hours_ago=7)

        fail_stuck_workflow_run(run, "swept")

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIsNotNone(run.completed_at)


class SweepTests(TestCase):
    def test_policies_are_registered_node_first(self):
        labels = [p.model_label for p in sweeps.all_policies()]
        self.assertIn("workflows.WorkflowNodeRun", labels)
        self.assertIn("workflows.WorkflowRun", labels)
        self.assertLess(labels.index("workflows.WorkflowNodeRun"),
                        labels.index("workflows.WorkflowRun"))

    def test_stuck_node_sweep_closes_node_and_run_together(self):
        run = make_run(hours_ago=4)
        node_run = make_node_run(run, hours_ago=4)

        closed = sweeps.run_sweeps()

        self.assertEqual(closed.get("workflows.WorkflowNodeRun"), 1)
        # The run was cascaded FAILED by the node closer, so the run policy
        # found nothing left to close.
        self.assertEqual(closed.get("workflows.WorkflowRun"), 0)
        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_nodeless_stuck_run_is_closed_by_the_run_policy(self):
        run = make_run(hours_ago=7)

        closed = sweeps.run_sweeps()

        self.assertEqual(closed.get("workflows.WorkflowRun"), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_fresh_rows_survive(self):
        run = make_run(hours_ago=0)
        node_run = make_node_run(run, hours_ago=0)

        sweeps.run_sweeps()

        run.refresh_from_db()
        node_run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.RUNNING)
        self.assertEqual(node_run.status, WorkflowNodeRun.RUNNING)
