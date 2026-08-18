"""Ownership: who owns what, who may change it, who started which run.

Named tests_ownership.py (sibling of tests.py) and listed explicitly in the
gate — ``toto`` is a namespace package, discovery finds nothing by itself.
Runs under zenobia settings (BUILD_WORKFLOWS=1 in the gate stanza).
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from .api import trigger_workflow
from .models import Workflow, WorkflowNode, WorkflowRun

User = get_user_model()


def make_users(test):
    test.alice = User.objects.create_user("alice", password="pw")
    test.bob = User.objects.create_user("bob", password="pw")
    test.staff = User.objects.create_user("staff", password="pw", is_staff=True)


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class AttributionTests(TestCase):
    def setUp(self):
        make_users(self)

    def test_api_create_sets_owner_and_cannot_be_spoofed(self):
        self.client.force_login(self.alice)
        response = self.client.post(
            reverse("workflows:api_workflow_list"),
            {"name": "mine", "owner": self.bob.pk},
            content_type="application/json")

        self.assertEqual(response.status_code, 201)
        workflow = Workflow.objects.get(name="mine")
        self.assertEqual(workflow.owner, self.alice)
        self.assertEqual(response.json()["owner_username"], "alice")

    def test_run_start_records_started_by(self):
        workflow = Workflow.objects.create(name="wf", owner=self.alice)
        WorkflowNode.objects.create(workflow=workflow,
                                    node_type=WorkflowNode.JOIN, label="j")
        self.client.force_login(self.bob)

        with patch("toto.workflows.views.celery_available", return_value=True), \
             patch("toto.workflows.views.start_workflow_run_task") as task:
            task.delay.return_value.id = "t-1"
            response = self.client.post(
                reverse("workflows:api_run_list", args=[workflow.pk]),
                {}, content_type="application/json")

        self.assertEqual(response.status_code, 201)
        run = WorkflowRun.objects.get()
        self.assertEqual(run.started_by, self.bob)  # a stranger MAY run it
        self.assertEqual(response.json()["started_by_username"], "bob")

    def test_trigger_workflow_records_and_omits_user(self):
        Workflow.objects.create(name="sys", slug="sys-flow")

        with patch("toto.workflows.tasks.start_workflow_run_task.delay"):
            attributed = trigger_workflow("sys-flow", {}, user=self.alice)
            anonymous = trigger_workflow("sys-flow", {})

        self.assertEqual(attributed.started_by, self.alice)
        self.assertIsNone(anonymous.started_by)

    def test_cancelled_is_a_real_choice(self):
        self.assertIn("cancelled", dict(WorkflowRun.STATUS_CHOICES))

    def test_system_workflow_serializes_null_owner(self):
        Workflow.objects.create(name="sys")
        self.client.force_login(self.alice)
        data = self.client.get(reverse("workflows:api_workflow_list")).json()
        self.assertIsNone(data[0]["owner"])
        self.assertIsNone(data[0]["owner_username"])


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class PermissionMatrixTests(TestCase):
    def setUp(self):
        make_users(self)
        self.mine = Workflow.objects.create(name="mine", owner=self.alice)
        self.system = Workflow.objects.create(name="system")

    def _patch(self, user, workflow):
        self.client.force_login(user)
        return self.client.patch(
            reverse("workflows:api_workflow_detail", args=[workflow.pk]),
            {"description": "x"}, content_type="application/json")

    def test_owner_may_edit_stranger_may_not_staff_may(self):
        self.assertEqual(self._patch(self.alice, self.mine).status_code, 200)
        self.assertEqual(self._patch(self.bob, self.mine).status_code, 403)
        self.assertEqual(self._patch(self.staff, self.mine).status_code, 200)

    def test_system_workflow_is_staff_only(self):
        self.assertEqual(self._patch(self.alice, self.system).status_code, 403)
        self.assertEqual(self._patch(self.staff, self.system).status_code, 200)

    def test_stranger_may_still_read(self):
        self.client.force_login(self.bob)
        response = self.client.get(
            reverse("workflows:api_workflow_detail", args=[self.mine.pk]))
        self.assertEqual(response.status_code, 200)

    def test_node_and_edge_writes_are_gated(self):
        self.client.force_login(self.bob)
        response = self.client.post(
            reverse("workflows:api_node_create", args=[self.mine.pk]),
            {"node_type": "join", "label": "j"},
            content_type="application/json")
        self.assertEqual(response.status_code, 403)

        node = WorkflowNode.objects.create(workflow=self.mine,
                                           node_type=WorkflowNode.JOIN, label="j")
        response = self.client.delete(
            reverse("workflows:api_node_detail", args=[self.mine.pk, node.pk]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.alice)
        response = self.client.delete(
            reverse("workflows:api_node_detail", args=[self.mine.pk, node.pk]))
        self.assertEqual(response.status_code, 204)

    def test_delete_clears_the_time_grant(self):
        from toto.tax.models import TimeGrant

        TimeGrant.objects.create(user=self.alice, key="workflows.lambda_timeout",
                                 scope_id=self.mine.pk, seconds=120)
        self.client.force_login(self.alice)

        response = self.client.delete(
            reverse("workflows:api_workflow_detail", args=[self.mine.pk]))

        self.assertEqual(response.status_code, 204)
        self.assertFalse(TimeGrant.objects.exists())


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class CancelMatrixTests(TestCase):
    def setUp(self):
        make_users(self)
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)
        self.run = WorkflowRun.objects.create(
            workflow=self.workflow, status=WorkflowRun.RUNNING,
            started_by=self.bob)

    def _cancel(self, user):
        self.client.force_login(user)
        return self.client.post(
            reverse("workflows:api_cancel_run", args=[self.run.pk]))

    def test_starter_owner_and_staff_may_cancel(self):
        for user in (self.bob, self.alice, self.staff):
            self.run.status = WorkflowRun.RUNNING
            self.run.save(update_fields=["status"])
            response = self._cancel(user)
            self.assertEqual(response.status_code, 200)
            self.run.refresh_from_db()
            self.assertEqual(self.run.status, WorkflowRun.CANCELLED)

    def test_stranger_gets_403_even_on_a_finished_run(self):
        stranger = User.objects.create_user("carol", password="pw")
        self.assertEqual(self._cancel(stranger).status_code, 403)

        self.run.status = WorkflowRun.COMPLETED
        self.run.save(update_fields=["status"])
        self.assertEqual(self._cancel(stranger).status_code, 403)

    def test_cancel_is_idempotent_for_the_privileged(self):
        self._cancel(self.bob)
        response = self._cancel(self.bob)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], WorkflowRun.CANCELLED)
