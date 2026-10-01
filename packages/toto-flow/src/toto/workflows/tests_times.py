"""The workflows.lambda_timeout dial: executor math, ownership, invariants.

Named tests_times.py (sibling of tests.py) and listed explicitly in the gate.
Runs under zenobia settings, where toto.tax is installed and the declaration
is autodiscovered.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from toto.quota import sweeps, times

from .models import LambdaFunction, Workflow, WorkflowNode, WorkflowNodeRun, WorkflowRun
from .services.executor import WorkflowExecutor

User = get_user_model()

KEY = "workflows.lambda_timeout"


def make_node_run(workflow):
    # No kernel since 2026-10-01: LambdaFunction lost its ComputeKernel link,
    # so a step's base budget is WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS alone.
    lambda_fn = LambdaFunction.objects.create(
        function_name=f"fn-{workflow.pk}", content="pass")
    node = WorkflowNode.objects.create(
        workflow=workflow, node_type=WorkflowNode.LAMBDA, label="l",
        lambda_function=lambda_fn)
    run = WorkflowRun.objects.create(workflow=workflow)
    return WorkflowNodeRun.objects.create(workflow_run=run, node=node,
                                          status=WorkflowNodeRun.PENDING)


class ExecutorDialTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)
        self.executor = WorkflowExecutor(async_lambdas=True)

    def _grant(self, seconds):
        from toto.tax.models import TimeGrant

        TimeGrant.objects.create(user=self.alice, key=KEY,
                                 scope_id=self.workflow.pk, seconds=seconds)

    def test_untouched_is_the_settings_default(self):
        node_run = make_node_run(self.workflow)
        self.assertEqual(self.executor._lambda_task_timeout_seconds(node_run), 30)

    @override_settings(WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS=3)
    def test_an_untouched_tight_setting_stays_tight(self):
        node_run = make_node_run(self.workflow)
        self.assertEqual(self.executor._lambda_task_timeout_seconds(node_run), 3)

    @override_settings(WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS=3)
    def test_raised_dial_floors_the_budget(self):
        self._grant(120)
        node_run = make_node_run(self.workflow)
        self.assertEqual(self.executor._lambda_task_timeout_seconds(node_run), 120)

    @override_settings(WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS=240)
    def test_a_larger_setting_still_wins(self):
        self._grant(120)
        node_run = make_node_run(self.workflow)
        self.assertEqual(self.executor._lambda_task_timeout_seconds(node_run), 240)

    @override_settings(WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS=7200)
    def test_runaway_setting_is_clamped(self):
        node_run = make_node_run(self.workflow)
        self.assertEqual(self.executor._lambda_task_timeout_seconds(node_run), 3600)


class NoKernelLinkTests(SimpleTestCase):
    """The retired notebooks hold nothing of workflows' (2026-10-01).

    LambdaFunction's one-to-one into mandragora.ComputeKernel, and the
    dependency of workflows' 0001 on mandragora's, were what kept the
    notebooks installed on every host that runs workflows.
    """

    def test_a_lambda_has_no_kernel(self):
        names = {field.name for field in LambdaFunction._meta.get_fields()}
        self.assertNotIn("kernel", names)
        related = {field.related_model._meta.app_label
                   for field in LambdaFunction._meta.get_fields()
                   if field.is_relation and field.related_model is not None}
        self.assertNotIn("mandragora", related)

    def test_no_workflows_migration_needs_mandragora(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        nodes = [key for key in loader.disk_migrations if key[0] == "workflows"]
        self.assertTrue(nodes)
        for key in nodes:
            with self.subTest(migration=key[1]):
                migration = loader.disk_migrations[key]
                self.assertNotIn("mandragora",
                                 {app for app, _ in migration.dependencies})


class DialOwnershipTests(TestCase):
    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)
        self.system = Workflow.objects.create(name="sys")

    def test_owner_sets_and_is_billed(self):
        from toto.tax import timegrants

        grant = timegrants.set_grant(actor=self.alice, key=KEY, seconds=120,
                                     scope_id=self.workflow.pk)
        self.assertEqual(grant.user, self.alice)
        self.assertEqual(
            times.effective_seconds(KEY, scope_id=self.workflow.pk), 120)

    def test_non_owner_is_refused(self):
        from toto.tax import timegrants

        with self.assertRaises(PermissionDenied):
            timegrants.set_grant(actor=self.bob, key=KEY, seconds=120,
                                 scope_id=self.workflow.pk)

    def test_system_workflow_refuses_everyone_including_staff(self):
        from toto.tax import timegrants

        # No staff bypass in _resolve_scope — deliberate: a system workflow
        # has no billing owner, so nobody can hold (or pay for) its dial.
        with self.assertRaises(PermissionDenied):
            timegrants.set_grant(actor=self.staff, key=KEY, seconds=120,
                                 scope_id=self.system.pk)

    def test_bounds(self):
        from toto.tax import timegrants

        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key=KEY, seconds=10,
                                 scope_id=self.workflow.pk)
        with self.assertRaises(ValidationError):
            timegrants.set_grant(actor=self.alice, key=KEY, seconds=9999,
                                 scope_id=self.workflow.pk)

    def test_levy_backstop_prunes_a_deleted_workflows_grant(self):
        from toto.quota.levy import registry as levy_registry
        from toto.tax.models import TimeGrant

        TimeGrant.objects.create(user=self.alice, key=KEY,
                                 scope_id=self.workflow.pk, seconds=120)
        self.workflow.delete()

        provider = levy_registry.get("time.hold")
        self.assertEqual(provider.measure(self.alice), 0)
        self.assertFalse(TimeGrant.objects.exists())


class InvariantTests(TestCase):
    def test_ceiling_fits_every_floor(self):
        decl = times.registry.get(KEY)
        self.assertIsNotNone(decl)
        self.assertEqual(decl.free_seconds, 30)
        self.assertLess(decl.ceiling_seconds + 5,
                        settings.CELERY_TASK_SOFT_TIME_LIMIT)
        self.assertLess(
            decl.ceiling_seconds + 5,
            settings.CELERY_BROKER_TRANSPORT_OPTIONS["visibility_timeout"])
        workflow_floors = [p.cutoff_seconds for p in sweeps.all_policies()
                          if p.model_label.startswith("workflows.")]
        self.assertTrue(workflow_floors)
        self.assertLess(decl.ceiling_seconds + 5, min(workflow_floors))


@override_settings(ROOT_URLCONF="toto.workflows.tests_urlconf")
class UiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )

    def setUp(self):
        self.alice = User.objects.create_user("alice", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.workflow = Workflow.objects.create(name="wf", owner=self.alice)

    def test_owner_sees_the_time_card_stranger_does_not(self):
        from django.urls import reverse

        url = reverse("workflows:workflow_detail", args=[self.workflow.pk])

        self.client.force_login(self.alice)
        content = self.client.get(url).content.decode()
        self.assertIn('value="workflows.lambda_timeout"', content)
        self.assertIn(f'name="scope_id" value="{self.workflow.pk}"', content)

        self.client.force_login(self.bob)
        content = self.client.get(url).content.decode()
        self.assertNotIn('value="workflows.lambda_timeout"', content)
        self.assertIn("alice", content)  # the owner badge renders
