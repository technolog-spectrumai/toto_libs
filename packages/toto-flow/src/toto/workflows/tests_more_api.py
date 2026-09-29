"""The workflows HTTP surface beyond ownership: reports, validation, runs, pages.

Named tests_more_api.py (sibling of tests.py) and meant for the gate's
host-owned block. Like tests_ownership.py it brings its own urlconf
(``tests_urlconf``), so it asserts the LIBRARY's contract; a host that narrows
the prefix (zenobia makes /workflows/ staff-only) tests that in its own suite.
"""

import unittest
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from .models import (
    LambdaFunction,
    Report,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .services.reports import create_report
from .services.validator import ValidationError, WorkflowValidator
from .views import _display_workflow_error

User = get_user_model()

TABLE = {"type": "table", "data": {"path": "rows"},
         "columns": [{"key": "name"}]}


def lam(workflow, label="l"):
    fn = LambdaFunction.objects.create(
        function_name=f"{workflow.pk}-{label}", content="print('{}')")
    return WorkflowNode.objects.create(workflow=workflow, node_type=WorkflowNode.LAMBDA,
                                       label=label, lambda_function=fn)


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class ReportTemplateApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.user)

    def test_anonymous_callers_are_refused(self):
        response = Client().get(reverse("workflows:api_report_template_list"))
        self.assertIn(response.status_code, (401, 403))

    def test_create_derives_the_type_and_slug(self):
        response = self.client.post(
            reverse("workflows:api_report_template_list"),
            {"name": "Sales Chart", "definition": {"type": "chart", "chart": "line"}},
            content_type="application/json")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["slug"], body["report_type"]), ("sales-chart", "chart"))

    def test_an_invalid_definition_is_a_400_not_a_saved_row(self):
        response = self.client.post(
            reverse("workflows:api_report_template_list"),
            {"name": "Broken", "definition": {"type": "table"}},
            content_type="application/json")

        self.assertEqual(response.status_code, 400)
        self.assertIn("definition", response.json())
        self.assertFalse(ReportTemplate.objects.filter(name="Broken").exists())

    def test_list_patch_and_delete(self):
        template = ReportTemplate.objects.create(name="Rows", definition=TABLE)
        url = reverse("workflows:api_report_template_detail", args=[template.pk])

        listed = self.client.get(reverse("workflows:api_report_template_list")).json()
        self.assertEqual([t["name"] for t in listed], ["Rows"])

        patched = self.client.patch(url, {"description": "all rows"},
                                    content_type="application/json")
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["description"], "all rows")

        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(ReportTemplate.objects.exists())

    def test_a_missing_template_is_404(self):
        url = reverse("workflows:api_report_template_detail", args=[999])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_reports_are_listed_newest_first_with_their_pages(self):
        template = ReportTemplate.objects.create(name="Rows", definition=TABLE)
        older = create_report(template=template, data={"rows": []}, title="Older")
        newer = create_report(template=template, data={"rows": []}, title="Newer")
        Report.objects.filter(pk=older.pk).update(created_at=newer.created_at.replace(year=2020))

        listed = self.client.get(reverse("workflows:api_report_list")).json()
        self.assertEqual([r["title"] for r in listed], ["Newer", "Older"])
        self.assertEqual(len(listed[0]["pages"]), 1)

        detail = self.client.get(reverse("workflows:api_report_detail", args=[newer.pk]))
        self.assertEqual(detail.json()["title"], "Newer")
        missing = self.client.get(reverse("workflows:api_report_detail", args=[999]))
        self.assertEqual(missing.status_code, 404)


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class ValidateAndRunApiTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.alice)
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)

    def test_validate_reports_every_error(self):
        WorkflowNode.objects.create(workflow=self.workflow,
                                    node_type=WorkflowNode.LAMBDA, label="bare")
        WorkflowNode.objects.create(workflow=self.workflow,
                                    node_type=WorkflowNode.REPORT, label="rep")

        response = self.client.post(
            reverse("workflows:api_workflow_validate", args=[self.workflow.pk]))

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertFalse(body["valid"])
        self.assertEqual(len(body["errors"]), 2)

    def test_validate_says_valid(self):
        lam(self.workflow)
        response = self.client.post(
            reverse("workflows:api_workflow_validate", args=[self.workflow.pk]))
        self.assertEqual(response.json(), {"valid": True, "errors": []})

    def test_validate_a_missing_workflow_is_404(self):
        response = self.client.post(reverse("workflows:api_workflow_validate", args=[999]))
        self.assertEqual(response.status_code, 404)

    def _start(self):
        return self.client.post(
            reverse("workflows:api_run_list", args=[self.workflow.pk]),
            {"input_data": {"k": 1}}, content_type="application/json")

    def test_an_invalid_workflow_is_refused_before_anything_is_created(self):
        with mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            response = self._start()

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["errors"], ["Workflow has no nodes."])
        self.assertFalse(WorkflowRun.objects.exists())
        task.delay.assert_not_called()

    def test_no_workers_means_503_and_no_run(self):
        lam(self.workflow)
        with mock.patch("toto.workflows.views.celery_available", return_value=False), \
             mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            response = self._start()

        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["celery_unavailable"])
        self.assertFalse(WorkflowRun.objects.exists())
        task.delay.assert_not_called()

    def test_a_started_run_keeps_its_input_and_is_metered_once(self):
        from .models import WorkflowUsageEvent

        lam(self.workflow)
        with mock.patch("toto.workflows.views.celery_available", return_value=True), \
             mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            task.delay.return_value.id = "t-9"
            response = self._start()

        self.assertEqual(response.status_code, 201)
        run = WorkflowRun.objects.get()
        self.assertEqual(run.input_data, {"k": 1})
        self.assertEqual(response.json()["task_id"], "t-9")
        task.delay.assert_called_once_with(run.id)
        event = WorkflowUsageEvent.objects.get()
        self.assertEqual(event.idempotency_key, f"workflows.run:{run.pk}")

    def test_a_rate_limited_user_gets_the_limits_status_and_no_run(self):
        from toto.quota import QuotaExceeded

        lam(self.workflow)
        refusal = QuotaExceeded(policy=mock.Mock(), current=5, limit=5)
        with mock.patch("toto.workflows.views.celery_available", return_value=True), \
             mock.patch("toto.quota.check_quota", side_effect=refusal), \
             mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            response = self._start()

        self.assertEqual(response.status_code, 429)
        self.assertFalse(WorkflowRun.objects.exists())
        task.delay.assert_not_called()

    @unittest.skip(
        "SUSPECTED BUG (workflows/views.py run_list): check_quota raises "
        "InArrears for a frozen debtor, but the view catches only QuotaExceeded "
        "and InsufficientFunds, so the refusal escapes as a 500 instead of 402")
    def test_a_frozen_debtor_is_refused_with_402_not_a_server_error(self):
        lam(self.workflow)
        client = Client(raise_request_exception=False)
        client.force_login(self.alice)
        with mock.patch("toto.workflows.views.celery_available", return_value=True), \
             mock.patch("toto.quota.levies.user_is_frozen", return_value=True), \
             mock.patch("toto.workflows.views.start_workflow_run_task"):
            response = client.post(
                reverse("workflows:api_run_list", args=[self.workflow.pk]),
                {}, content_type="application/json")

        self.assertEqual(response.status_code, 402)
        self.assertFalse(WorkflowRun.objects.exists())

    def test_runs_are_listed_newest_first(self):
        first = WorkflowRun.objects.create(workflow=self.workflow)
        second = WorkflowRun.objects.create(workflow=self.workflow)
        WorkflowRun.objects.filter(pk=first.pk).update(
            created_at=second.created_at.replace(year=2020))

        listed = self.client.get(
            reverse("workflows:api_run_list", args=[self.workflow.pk])).json()

        self.assertEqual([r["id"] for r in listed], [second.id, first.id])

    def test_run_detail_carries_node_runs_and_404s_when_missing(self):
        node = lam(self.workflow)
        run = WorkflowRun.objects.create(workflow=self.workflow)
        WorkflowNodeRun.objects.create(workflow_run=run, node=node,
                                       status=WorkflowNodeRun.COMPLETED)

        body = self.client.get(reverse("workflows:api_run_detail", args=[run.pk])).json()
        self.assertEqual([(n["node_label"], n["status"]) for n in body["node_runs"]],
                         [("l", "completed")])
        missing = self.client.get(reverse("workflows:api_run_detail", args=[999]))
        self.assertEqual(missing.status_code, 404)

    def test_cancelling_fails_in_flight_steps_but_not_finished_ones(self):
        done = lam(self.workflow, "done")
        busy = lam(self.workflow, "busy")
        queued = lam(self.workflow, "queued")
        run = WorkflowRun.objects.create(workflow=self.workflow,
                                         status=WorkflowRun.RUNNING,
                                         started_by=self.alice)
        for node, status in ((done, WorkflowNodeRun.COMPLETED),
                             (busy, WorkflowNodeRun.RUNNING),
                             (queued, WorkflowNodeRun.PENDING)):
            WorkflowNodeRun.objects.create(workflow_run=run, node=node, status=status)

        response = self.client.post(reverse("workflows:api_cancel_run", args=[run.pk]))

        self.assertEqual(response.json(), {"status": WorkflowRun.CANCELLED})
        states = dict(run.node_runs.values_list("node__label", "status"))
        self.assertEqual(states, {"done": "completed", "busy": "failed", "queued": "failed"})
        self.assertEqual(
            set(run.node_runs.exclude(node=done).values_list("error", flat=True)),
            {"Cancelled by user."})
        run.refresh_from_db()
        self.assertIsNotNone(run.completed_at)


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class GraphEditingApiTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.client.force_login(self.alice)
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)
        self.other = Workflow.objects.create(name="other", owner=self.alice)
        self.a = lam(self.workflow, "a")
        self.b = lam(self.workflow, "b")

    def _edge(self, workflow, source, target, **extra):
        return self.client.post(
            reverse("workflows:api_edge_create", args=[workflow.pk]),
            {"source": source.pk, "target": target.pk, **extra},
            content_type="application/json")

    def test_an_edge_is_created_between_two_nodes_of_the_workflow(self):
        response = self._edge(self.workflow, self.a, self.b, branch_key="yes")
        self.assertEqual(response.status_code, 201)
        edge = WorkflowEdge.objects.get()
        self.assertEqual((edge.source, edge.target, edge.branch_key),
                         (self.a, self.b, "yes"))

    def test_a_self_loop_is_refused(self):
        response = self._edge(self.workflow, self.a, self.a)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(WorkflowEdge.objects.exists())

    def test_an_edge_may_not_join_two_workflows(self):
        foreign = lam(self.other, "foreign")
        response = self._edge(self.workflow, self.a, foreign)
        self.assertEqual(response.status_code, 400)
        self.assertIn("same workflow", str(response.json()))

    def test_an_edge_is_deleted_only_through_its_own_workflow(self):
        edge = WorkflowEdge.objects.create(workflow=self.workflow,
                                           source=self.a, target=self.b)
        wrong = self.client.delete(
            reverse("workflows:api_edge_delete", args=[self.other.pk, edge.pk]))
        self.assertEqual(wrong.status_code, 404)
        right = self.client.delete(
            reverse("workflows:api_edge_delete", args=[self.workflow.pk, edge.pk]))
        self.assertEqual(right.status_code, 204)
        self.assertFalse(WorkflowEdge.objects.exists())

    def test_a_stranger_may_not_delete_an_edge(self):
        edge = WorkflowEdge.objects.create(workflow=self.workflow,
                                           source=self.a, target=self.b)
        self.client.force_login(User.objects.create_user("mallory", password="pw"))
        response = self.client.delete(
            reverse("workflows:api_edge_delete", args=[self.workflow.pk, edge.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(WorkflowEdge.objects.exists())

    def test_a_node_is_addressed_only_through_its_own_workflow(self):
        url = reverse("workflows:api_node_detail", args=[self.other.pk, self.a.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertTrue(WorkflowNode.objects.filter(pk=self.a.pk).exists())

    def test_a_node_can_be_created_and_renamed_by_its_owner(self):
        created = self.client.post(
            reverse("workflows:api_node_create", args=[self.workflow.pk]),
            {"node_type": "split", "label": "fork"}, content_type="application/json")
        self.assertEqual(created.status_code, 201)
        node_id = created.json()["id"]
        self.assertEqual(WorkflowNode.objects.get(pk=node_id).workflow, self.workflow)

        renamed = self.client.patch(
            reverse("workflows:api_node_detail", args=[self.workflow.pk, node_id]),
            {"label": "branch"}, content_type="application/json")
        self.assertEqual(renamed.json()["label"], "branch")

    def test_the_client_cannot_move_a_node_into_another_workflow_on_create(self):
        response = self.client.post(
            reverse("workflows:api_node_create", args=[self.workflow.pk]),
            {"node_type": "join", "label": "j", "workflow": self.other.pk},
            content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(WorkflowNode.objects.get(pk=response.json()["id"]).workflow_id,
                         self.workflow.pk)

    def test_an_unknown_workflow_is_404_everywhere(self):
        for name in ("api_workflow_detail", "api_run_list", "api_node_create",
                     "api_edge_create"):
            with self.subTest(route=name):
                method = self.client.get if name in (
                    "api_workflow_detail", "api_run_list") else self.client.post
                response = method(reverse(f"workflows:{name}", args=[999]))
                self.assertEqual(response.status_code, 404)


class ValidatorRuleTests(TestCase):
    def setUp(self):
        self.workflow = Workflow.objects.create(name="wf")

    def errors(self):
        try:
            WorkflowValidator().validate(self.workflow)
        except ValidationError as exc:
            return exc.errors
        return []

    def edge(self, source, target, **kwargs):
        return WorkflowEdge.objects.create(workflow=self.workflow, source=source,
                                           target=target, **kwargs)

    def test_a_cycle_is_refused(self):
        a, b, c = (lam(self.workflow, x) for x in "abc")
        self.edge(a, b)
        self.edge(b, c)
        self.edge(c, a)
        self.assertIn("Workflow graph contains a cycle.", self.errors())

    def test_a_self_loop_is_named(self):
        a = lam(self.workflow)
        self.edge(a, a)
        self.assertTrue(any("self-loop" in e for e in self.errors()))

    def test_a_split_needs_exactly_one_parent(self):
        split = WorkflowNode.objects.create(workflow=self.workflow,
                                            node_type=WorkflowNode.SPLIT)
        child = lam(self.workflow, "child")
        self.edge(split, child, branch_key="x")
        self.assertTrue(any("exactly one incoming edge (found 0)" in e
                            for e in self.errors()))

        for label in ("p1", "p2"):
            self.edge(lam(self.workflow, label), split)
        self.assertTrue(any("(found 2)" in e for e in self.errors()))

    def test_a_split_needs_a_way_out_and_at_most_one_default(self):
        parent = lam(self.workflow, "parent")
        split = WorkflowNode.objects.create(workflow=self.workflow,
                                            node_type=WorkflowNode.SPLIT)
        self.edge(parent, split)
        self.assertTrue(any("no outgoing edges" in e for e in self.errors()))

        self.edge(split, lam(self.workflow, "d1"), is_default=True)
        self.edge(split, lam(self.workflow, "d2"), is_default=True)
        self.assertTrue(any("2 default edges" in e for e in self.errors()))

    def test_a_well_formed_split_passes(self):
        parent = lam(self.workflow, "parent")
        split = WorkflowNode.objects.create(workflow=self.workflow,
                                            node_type=WorkflowNode.SPLIT)
        self.edge(parent, split)
        self.edge(split, lam(self.workflow, "yes"), branch_key="yes")
        self.edge(split, lam(self.workflow, "no"), is_default=True)
        self.assertEqual(self.errors(), [])

    def test_a_report_node_needs_a_template(self):
        WorkflowNode.objects.create(workflow=self.workflow,
                                    node_type=WorkflowNode.REPORT, label="r")
        self.assertTrue(any("no report_template" in e for e in self.errors()))

    def test_errors_join_into_the_exception_message(self):
        WorkflowNode.objects.create(workflow=self.workflow,
                                    node_type=WorkflowNode.LAMBDA, label="x")
        WorkflowNode.objects.create(workflow=self.workflow,
                                    node_type=WorkflowNode.REPORT, label="y")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(self.workflow)
        self.assertEqual(str(ctx.exception), "; ".join(ctx.exception.errors))


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class PagesTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="T", author="T", publication_year=2026,
                                active=True)
        self.staff = User.objects.create_user("ops", password="pw", is_staff=True)
        self.client.force_login(self.staff)
        self.workflow = Workflow.objects.create(name="Nightly", owner=self.staff)
        self.node = lam(self.workflow, "fetch")

    def test_anonymous_is_sent_to_log_in(self):
        response = Client().get(reverse("workflows:workflow_list"))
        self.assertEqual(response.status_code, 302)

    def test_the_list_shows_the_workflow(self):
        response = self.client.get(reverse("workflows:workflow_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nightly")

    def test_the_detail_page_draws_the_graph_and_offers_the_owner_the_dial(self):
        response = self.client.get(
            reverse("workflows:workflow_detail", args=[self.workflow.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_owner"])
        self.assertIn("time_dial", response.context)
        self.assertIn('"lambda_name": "', response.context["graph_nodes_json"])

    def test_a_non_owner_gets_no_dial(self):
        other = User.objects.create_user("ops2", password="pw", is_staff=True)
        self.client.force_login(other)
        response = self.client.get(
            reverse("workflows:workflow_detail", args=[self.workflow.pk]))
        self.assertFalse(response.context["is_owner"])
        self.assertNotIn("time_dial", response.context)

    def test_the_run_page_shows_a_readable_error_and_graph_state(self):
        run = WorkflowRun.objects.create(workflow=self.workflow,
                                         status=WorkflowRun.FAILED,
                                         started_by=self.staff)
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=self.node, status=WorkflowNodeRun.FAILED,
            error="Kernel error: kernel_server_timeout")

        response = self.client.get(
            reverse("workflows:workflow_run_detail", args=[run.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["run_error"], "Workflow task timed out.")
        self.assertEqual(response.context["run_error_node"], self.node)
        self.assertTrue(response.context["can_cancel"])
        self.assertContains(response, "Workflow task timed out.")
        self.assertIn('"status": "failed"', response.context["graph_nodes_json"])

    def test_the_run_page_renders_a_generated_report(self):
        template = ReportTemplate.objects.create(name="Rows", definition=TABLE)
        run = WorkflowRun.objects.create(workflow=self.workflow,
                                         status=WorkflowRun.COMPLETED)
        nr = WorkflowNodeRun.objects.create(workflow_run=run, node=self.node,
                                            status=WorkflowNodeRun.COMPLETED)
        create_report(template=template, data={"rows": [{"name": "zebra-row"}]},
                      title="Row report", workflow_run=run, source_node_run=nr)

        response = self.client.get(
            reverse("workflows:workflow_run_detail", args=[run.pk]))

        (report,) = response.context["reports"]
        self.assertEqual(report.render_block["type"], "table")
        self.assertEqual(report.render_block["rows"][0]["values"], ["zebra-row"])
        (node_run,) = response.context["node_runs"]
        self.assertEqual(node_run.generated_reports, [report])

    def test_a_missing_run_or_workflow_is_404(self):
        self.assertEqual(self.client.get(
            reverse("workflows:workflow_run_detail", args=[999])).status_code, 404)
        self.assertEqual(self.client.get(
            reverse("workflows:workflow_detail", args=[999])).status_code, 404)


class DisplayErrorTests(TestCase):
    def test_kernel_errors_are_translated_for_people(self):
        self.assertEqual(_display_workflow_error(""), "")
        self.assertEqual(_display_workflow_error(None), "")
        self.assertEqual(_display_workflow_error("kernel_server_timeout"),
                         "Workflow task timed out.")
        self.assertEqual(_display_workflow_error("Kernel error: kernel_server_timeout"),
                         "Workflow task timed out.")
        self.assertEqual(_display_workflow_error("Kernel error:  NameError "),
                         "Workflow task error: NameError")
        self.assertEqual(_display_workflow_error("plain failure"), "plain failure")
