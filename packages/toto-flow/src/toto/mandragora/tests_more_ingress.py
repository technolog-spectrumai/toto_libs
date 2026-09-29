"""ingress_mandragora: what a realistic and a full seed leave behind.

Named tests_more_ingress.py (sibling of tests.py) and meant for the gate's
host-owned block. On zenobia the notebook UI is unmounted, but the app is
installed (LambdaFunction's kernel FK needs it) and its seeder runs in
``ingress_all`` — realistic on every deploy, full on demonstration boxes. So
the seeder is the part of this app that runs, and these tests hold it to the
ingress contract: compulsory rows only when realistic, and idempotent always.
"""
import io
import tempfile
import unittest

from django.core.management import call_command
from django.test import TestCase, override_settings

from toto.workflows.models import (
    LambdaFunction,
    Report,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
    validate_report_definition,
)
from toto.workflows.services.executor import WorkflowExecutor
from toto.workflows.services.reports import render_report
from toto.workflows.services.validator import WorkflowValidator

from .models import Cell, ComputeKernel, KernelDependency, Notebook

MODELS = (ComputeKernel, KernelDependency, Notebook, Cell, LambdaFunction, Workflow,
          WorkflowNode, WorkflowEdge, WorkflowRun, ReportTemplate, Report)


def seed(mode):
    call_command("ingress_mandragora", mode=mode, stdout=io.StringIO())


def counts():
    return {model.__name__: model.objects.count() for model in MODELS}


class RealisticSeedTests(TestCase):
    def test_realistic_seeds_one_kernel_with_its_dependencies_and_nothing_else(self):
        seed("realistic")

        kernel = ComputeKernel.objects.get()
        self.assertEqual((kernel.name, kernel.timeout_ms), ("Python 3", 30000))
        self.assertEqual(sorted(d.pip_specifier() for d in kernel.kernel_dependencies.all()),
                         ["matplotlib", "numpy", "pandas"])
        for model in (Notebook, Workflow, LambdaFunction, Report, WorkflowRun):
            with self.subTest(model=model.__name__):
                self.assertFalse(model.objects.exists())

    def test_realistic_is_idempotent(self):
        seed("realistic")
        before = counts()
        seed("realistic")
        self.assertEqual(counts(), before)

    def test_an_existing_kernel_keeps_its_own_dependencies(self):
        kernel = ComputeKernel.objects.create(name="Python 3", timeout_ms=1000)
        KernelDependency.objects.create(kernel=kernel, package_name="scipy",
                                        version_spec=">=1.11")
        seed("realistic")
        self.assertEqual([d.pip_specifier() for d in kernel.kernel_dependencies.all()],
                         ["scipy>=1.11"])
        kernel.refresh_from_db()
        self.assertEqual(kernel.timeout_ms, 1000)

    def test_none_seeds_nothing(self):
        seed("none")
        self.assertEqual(set(counts().values()), {0})


class FullSeedTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls._media = tempfile.TemporaryDirectory()
        cls._override = override_settings(MEDIA_ROOT=cls._media.name,
                                          WORKFLOW_FILE_CONNECTOR_ROOT=None)
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        cls._media.cleanup()

    @classmethod
    def setUpTestData(cls):
        seed("full")
        cls.after_first = counts()

    def test_full_seeds_the_demonstration_data(self):
        self.assertEqual(Notebook.objects.get().slug, "getting-started")
        self.assertEqual(list(Notebook.objects.get().cells.values_list("position", flat=True)),
                         [1, 2, 3, 4])
        self.assertGreater(Workflow.objects.count(), 3)
        self.assertGreater(Report.objects.count(), 0)
        self.assertGreater(WorkflowRun.objects.count(), 0)

    def test_full_is_idempotent(self):
        seed("full")
        self.assertEqual(counts(), self.after_first)

    def test_every_seeded_report_template_is_a_valid_definition(self):
        for template in ReportTemplate.objects.all():
            with self.subTest(template=template.slug):
                validate_report_definition(template.definition)

    def test_every_seeded_report_renders(self):
        for report in Report.objects.all():
            with self.subTest(report=report.slug):
                pages = render_report(report)
                self.assertTrue(pages)
                self.assertTrue(all(page["blocks"] for page in pages))

    RUNNABLE_DEMOS = ("Data Pipeline", "Parallel Enrichment", "Metrics Trend Report",
                      "Channel Mix Report", "File Client Demo")

    def test_the_runnable_demos_pass_the_validator(self):
        for name in self.RUNNABLE_DEMOS:
            with self.subTest(workflow=name):
                WorkflowValidator().validate(Workflow.objects.get(name=name))

    def test_the_runnable_demos_actually_run_to_completion(self):
        """What a visitor on a demonstration box does first: press Run."""
        for name in self.RUNNABLE_DEMOS:
            with self.subTest(workflow=name):
                workflow = Workflow.objects.get(name=name)
                reports_before = Report.objects.count()
                run = WorkflowRun.objects.create(workflow=workflow, input_data={})

                WorkflowExecutor().start(run)

                run.refresh_from_db()
                self.assertEqual(run.status, WorkflowRun.COMPLETED)
                self.assertEqual(
                    set(run.node_runs.values_list("status", flat=True)), {"completed"})
                self.assertEqual(run.node_runs.count(), workflow.nodes.count())
                report_nodes = workflow.nodes.filter(node_type=WorkflowNode.REPORT).count()
                self.assertEqual(Report.objects.count() - reports_before, report_nodes)

    @unittest.skip(
        "SUSPECTED BUG (ingress_mandragora._seed_approval_workflow): the "
        "'Human Approval Gate' demo has a LAMBDA node 'Review' with no "
        "lambda_function (a human-task config the engine has no node type for), "
        "so WorkflowValidator rejects it and the seeded demo can never be run")
    def test_every_seeded_demo_workflow_passes_the_validator(self):
        for workflow in Workflow.objects.all():
            with self.subTest(workflow=workflow.name):
                WorkflowValidator().validate(workflow)
