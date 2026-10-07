"""Dispatch-only nodes: started by their app's dispatcher, never by hand.

`register(name, dispatch_only=True)` marks a node that trusts its input to
name a record somebody already claimed (a forum cleanup, a scan, a vault
refresh or transfer). `api_run_list` — the one door that starts a run by hand
— refuses any workflow containing one, for staff and members alike, so nobody
can replay another person's run with input of their own choosing. Listing the
workflow and its runs is unaffected.

The view is called through DRF's request factory as well as the client: a host
may put its own staff gate in front of /workflows/ (zenobia does), and the
library's guard must hold without it.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIRequestFactory, force_authenticate

from .models import Workflow, WorkflowNode, WorkflowRun
from .predefined_tasks import (_dispatch_only, _registry, dispatch_only_tasks,
                               is_dispatch_only, register)
from .views import run_list

User = get_user_model()


class DispatchOnlyBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(username="staff", password="x",
                                             is_staff=True)
        cls.member = User.objects.create_user(username="member", password="x")

    def setUp(self):
        self._saved = (dict(_registry), set(_dispatch_only))

        @register("tests_guarded", dispatch_only=True)
        def guarded(input_data):
            return {"data": {}}

        @register("tests_open")
        def open_(input_data):
            return {"data": {}}

    def tearDown(self):
        _registry.clear()
        _registry.update(self._saved[0])
        _dispatch_only.clear()
        _dispatch_only.update(self._saved[1])

    def _workflow(self, *task_names, owner=None):
        workflow = Workflow.objects.create(name="wf", owner=owner)
        for name in task_names:
            WorkflowNode.objects.create(
                workflow=workflow, node_type=WorkflowNode.PREDEFINED_TASK,
                task_name=name, label=name)
        return workflow

    def _post(self, user, workflow):
        request = APIRequestFactory().post(
            f"/workflows/api/{workflow.pk}/runs/", {"input_data": {}},
            format="json")
        force_authenticate(request, user=user)
        return run_list(request, workflow_id=workflow.pk)


class RegistryTests(DispatchOnlyBase):
    def test_the_flag_is_recorded_and_defaults_off(self):
        self.assertTrue(is_dispatch_only("tests_guarded"))
        self.assertFalse(is_dispatch_only("tests_open"))
        self.assertFalse(is_dispatch_only("no_such_task"))

    def test_re_registering_without_the_flag_clears_it(self):
        register("tests_guarded")(lambda data: data)
        self.assertFalse(is_dispatch_only("tests_guarded"))

    def test_the_helper_names_the_guarded_nodes(self):
        self.assertEqual(dispatch_only_tasks(
            self._workflow("tests_open", "tests_guarded")), ["tests_guarded"])
        self.assertEqual(dispatch_only_tasks(self._workflow("tests_open")), [])
        self.assertEqual(dispatch_only_tasks(self._workflow()), [])


class StartDoorTests(DispatchOnlyBase):
    def test_staff_and_members_alike_are_refused(self):
        workflow = self._workflow("tests_guarded", owner=self.member)
        for user in (self.staff, self.member):
            with self.subTest(user=user.username), \
                 mock.patch("toto.workflows.views.celery_available",
                            return_value=True), \
                 mock.patch("toto.workflows.views.start_workflow_run_task") as task:
                response = self._post(user, workflow)
                self.assertEqual(response.status_code, 403)
                self.assertIn("never by hand", str(response.data["detail"]))
                task.delay.assert_not_called()
        self.assertFalse(WorkflowRun.objects.exists())

    def test_one_guarded_node_among_others_is_enough(self):
        workflow = self._workflow("tests_open", "tests_guarded")
        with mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            response = self._post(self.staff, workflow)
        self.assertEqual(response.status_code, 403)
        task.delay.assert_not_called()

    def test_it_refuses_before_charging_anything(self):
        workflow = self._workflow("tests_guarded")
        with mock.patch("toto.quota.check_quota") as check_quota, \
             mock.patch("toto.quota.charge.charge") as charge:
            self._post(self.staff, workflow)
        check_quota.assert_not_called()
        charge.assert_not_called()

    def test_an_ordinary_workflow_still_starts(self):
        workflow = self._workflow("tests_open")
        with mock.patch("toto.workflows.views.celery_available",
                        return_value=True), \
             mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            task.delay.return_value.id = "t-1"
            response = self._post(self.staff, workflow)
        self.assertEqual(response.status_code, 201)
        task.delay.assert_called_once()

    def test_listing_its_runs_is_unaffected(self):
        workflow = self._workflow("tests_guarded")
        run = WorkflowRun.objects.create(workflow=workflow, input_data={})
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse("workflows:api_run_list", args=[workflow.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r["id"] for r in response.json()], [run.pk])


class MarkedNodesTests(TestCase):
    """The platform's own dispatched nodes carry the flag, where installed."""

    def test_the_dispatched_nodes_are_marked(self):
        from django.apps import apps

        expected = {
            # Back since stage 69 (2026-10-07), with the simplified forum's
            # cleanup: the node finishes only records its dispatcher claimed.
            "toto.forum": ["forum_cleanup"],
            "toto.antivirus": ["antivirus_scan"],
            "toto.vault": ["vault_refresh_remote_bucket",
                           "vault_transfer_files", "vault_zip_files"],
        }
        checked = 0
        for app, names in expected.items():
            if not apps.is_installed(app):
                continue
            for name in names:
                with self.subTest(task=name):
                    self.assertTrue(is_dispatch_only(name))
                    checked += 1
        if not checked:
            self.skipTest("none of the dispatching apps is installed")

    def test_every_node_that_reads_a_record_id_is_marked(self):
        """Stage 51: `vault_zip_files` trusted owner_id, directory and file ids
        typed into the Workflows API, so a staff account zipped a member's
        private files into an archive it owned. A node that reads a record id
        from its input is started by its app, which checked the record first;
        the start door must never take one by hand."""
        import inspect
        import re

        # Read no record id, or refuse every input: nothing to replay.
        hand_startable = {"vault_encrypt_file"}
        reads_an_id = re.compile(r"""["'][a-z_]*_ids?["']""")
        for name, handler in sorted(_registry.items()):
            if name in hand_startable:
                continue
            try:
                source = inspect.getsource(handler)
            except (OSError, TypeError):
                continue
            if reads_an_id.search(source):
                with self.subTest(task=name):
                    self.assertTrue(is_dispatch_only(name),
                                    f"{name} reads a record id from its input")


class VaultZipDoorTests(DispatchOnlyBase):
    """The vault's own zip workflow, seeded as CreateZipView seeds it."""

    def test_staff_cannot_start_a_zip_run_by_hand(self):
        from django.apps import apps
        if not apps.is_installed("toto.vault"):
            self.skipTest("toto.vault is not installed")
        from toto.vault.views import CreateZipView

        workflow = CreateZipView._ensure_workflow()
        request = APIRequestFactory().post(
            f"/workflows/api/{workflow.pk}/runs/",
            {"input_data": {"data": {"owner_id": self.staff.pk,
                                     "source_directory_id": 1,
                                     "file_ids": [1], "output_name": "x.zip"}}},
            format="json")
        force_authenticate(request, user=self.staff)
        with mock.patch("toto.workflows.views.celery_available",
                        return_value=True), \
             mock.patch("toto.workflows.views.start_workflow_run_task") as task:
            response = run_list(request, workflow_id=workflow.pk)
        self.assertEqual(response.status_code, 403)
        task.delay.assert_not_called()
        self.assertFalse(WorkflowRun.objects.filter(workflow=workflow).exists())
