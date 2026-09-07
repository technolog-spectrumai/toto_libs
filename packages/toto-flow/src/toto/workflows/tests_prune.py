"""Pruning the demo workflows without taking a working feature with them."""

from __future__ import annotations

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from toto.workflows.management.commands.prune_demo_workflows import (
    DEMO_SLUGS, FUNCTIONAL_SLUGS,
)
from toto.workflows.models import Workflow


def run(*args):
    out, err = StringIO(), StringIO()
    call_command("prune_demo_workflows", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


class PruneDemoWorkflowsTests(TestCase):
    def setUp(self):
        for slug in DEMO_SLUGS:
            Workflow.objects.create(name=slug.replace("-", " ").title(), slug=slug)
        for slug in FUNCTIONAL_SLUGS:
            Workflow.objects.create(name=slug, slug=slug)

    def test_a_dry_run_removes_nothing(self):
        """Deleting cascades to runs — the default must not do that."""
        out, _ = run()
        self.assertIn("Dry run", out)
        self.assertEqual(Workflow.objects.count(), len(DEMO_SLUGS)
                         + len(FUNCTIONAL_SLUGS))

    def test_delete_removes_every_demo(self):
        run("--delete")
        left = set(Workflow.objects.values_list("slug", flat=True))
        self.assertEqual(left, set(FUNCTIONAL_SLUGS))

    def test_the_functional_workflows_survive(self):
        """vault-zip, repo-run and antivirus-scan are looked up by slug at
        runtime; removing one breaks a button rather than tidying a list."""
        run("--delete")
        for slug in FUNCTIONAL_SLUGS:
            self.assertTrue(Workflow.objects.filter(slug=slug).exists(), slug)

    def test_naming_a_functional_slug_is_refused(self):
        _, err = run("--slug", "vault-zip", "--delete")
        self.assertIn("refused", err)
        self.assertTrue(Workflow.objects.filter(slug="vault-zip").exists())

    def test_one_bad_slug_refuses_the_whole_run(self):
        """Whole, not partly: a half-applied delete is worse than none."""
        _, err = run("--slug", "data-pipeline", "--slug", "repo-run", "--delete")
        self.assertIn("refused", err)
        self.assertTrue(Workflow.objects.filter(slug="data-pipeline").exists())

    def test_a_named_demo_slug_is_removed(self):
        run("--slug", "data-pipeline", "--delete")
        self.assertFalse(Workflow.objects.filter(slug="data-pipeline").exists())
        self.assertTrue(Workflow.objects.filter(slug="parallel-enrichment").exists())

    def test_a_clean_database_says_so(self):
        Workflow.objects.filter(slug__in=DEMO_SLUGS).delete()
        out, _ = run("--delete")
        self.assertIn("Nothing to remove", out)

    def test_running_twice_is_safe(self):
        run("--delete")
        out, _ = run("--delete")
        self.assertIn("Nothing to remove", out)

    def test_the_demo_list_and_the_functional_list_never_overlap(self):
        """The guard only works while these stay disjoint."""
        self.assertEqual(set(DEMO_SLUGS) & set(FUNCTIONAL_SLUGS), set())
