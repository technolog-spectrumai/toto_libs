"""toto.repo ⇄ workflows integration: ingress seeding, the repo_run
predefined task, executor status propagation, and the three dispatch paths.

Requires toto.workflows installed (BUILD_REPO forces BUILD_WORKFLOWS)."""

from unittest import mock

from django.core.management import call_command

from toto.repo import services
from toto.repo.dispatch import dispatch_git_run
from toto.repo.models import GitRun
from toto.repo.predefined_tasks import repo_run
from toto.workflows.models import Workflow, WorkflowNode, WorkflowRun
from toto.workflows.tasks import start_workflow_run_task

from .base import RepoTestCase


class IngressTests(RepoTestCase):
    def test_seeds_run_workflow(self):
        call_command("ingress_repo")
        wf = Workflow.objects.get(slug="repo-run")
        self.assertTrue(
            wf.nodes.filter(
                node_type=WorkflowNode.PREDEFINED_TASK, task_name="repo_run"
            ).exists()
        )

    def test_ingress_is_idempotent(self):
        call_command("ingress_repo")
        call_command("ingress_repo")
        self.assertEqual(Workflow.objects.filter(slug="repo-run").count(), 1)
        self.assertEqual(
            Workflow.objects.get(slug="repo-run").nodes.count(), 1
        )

    def test_ingress_repairs_missing_node(self):
        call_command("ingress_repo")
        Workflow.objects.get(slug="repo-run").nodes.all().delete()
        call_command("ingress_repo")
        self.assertTrue(
            Workflow.objects.get(slug="repo-run")
            .nodes.filter(task_name="repo_run").exists()
        )


class PredefinedTaskTests(RepoTestCase):
    def test_requires_run_id(self):
        with self.assertRaises(ValueError):
            repo_run({"data": {}})

    def test_successful_init_run(self):
        repo = services.create_repo(self.root, self.user)
        run = GitRun.objects.create(repo=repo, user=self.user, op="init")
        result = repo_run({"data": {"run_id": run.pk}})
        run.refresh_from_db()
        self.assertEqual(run.status, GitRun.SUCCESS)
        self.assertEqual(result["data"]["status"], GitRun.SUCCESS)
        self.assertIn("initialized", run.stdout)
        # worktree materialized with an initial commit
        from toto.repo import git_cli
        self.assertEqual(len(git_cli.log_all(repo.worktree)), 1)

    def test_failed_run_raises_for_honest_workflow_status(self):
        # push on an unconnected/Gitea-less deployment fails inside the runner
        repo = self.make_repo()
        run = GitRun.objects.create(repo=repo, user=self.user, op="push")
        with self.assertRaises(RuntimeError):
            repo_run({"data": {"run_id": run.pk}})
        run.refresh_from_db()
        self.assertEqual(run.status, GitRun.FAILED)


class ExecutorStatusTests(RepoTestCase):
    """Full engine pass (synchronous call, no broker) — WorkflowRun status
    must mirror the git outcome."""

    def _wf_run_for(self, git_run):
        call_command("ingress_repo")
        wf = Workflow.objects.get(slug="repo-run")
        return WorkflowRun.objects.create(
            workflow=wf, input_data={"data": {"run_id": git_run.pk}}
        )

    def test_workflow_run_completed_on_success(self):
        repo = services.create_repo(self.root, self.user)
        run = GitRun.objects.create(repo=repo, user=self.user, op="init")
        wf_run = self._wf_run_for(run)
        start_workflow_run_task(wf_run.pk)
        wf_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(wf_run.status, WorkflowRun.COMPLETED)
        self.assertEqual(run.status, GitRun.SUCCESS)

    def test_workflow_run_failed_on_git_failure(self):
        repo = self.make_repo()
        run = GitRun.objects.create(repo=repo, user=self.user, op="push")
        wf_run = self._wf_run_for(run)
        start_workflow_run_task(wf_run.pk)
        wf_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(wf_run.status, WorkflowRun.FAILED)
        self.assertEqual(run.status, GitRun.FAILED)


class DispatchTests(RepoTestCase):
    def _run(self, op="init"):
        repo = services.create_repo(self.sub, self.user)
        return GitRun.objects.create(repo=repo, user=self.user, op=op)

    @mock.patch("toto.repo.dispatch.celery_available", return_value=True)
    @mock.patch("toto.workflows.tasks.start_workflow_run_task.delay")
    def test_workflow_path(self, delay, _avail):
        call_command("ingress_repo")
        run = self._run()
        self.assertTrue(dispatch_git_run(run))
        run.refresh_from_db()
        self.assertIsNotNone(run.workflow_run)
        self.assertEqual(
            run.workflow_run.input_data, {"data": {"run_id": run.pk}}
        )
        delay.assert_called_once_with(run.workflow_run.pk)

    @mock.patch("toto.repo.dispatch.celery_available", return_value=True)
    @mock.patch("toto.repo.tasks.run_git_task.delay")
    def test_bare_task_fallback_when_workflow_missing(self, delay, _avail):
        run = self._run()
        self.assertTrue(dispatch_git_run(run))
        run.refresh_from_db()
        self.assertIsNone(run.workflow_run)
        delay.assert_called_once_with(run.pk)

    @mock.patch("toto.repo.dispatch.celery_available", return_value=False)
    def test_inline_path(self, _avail):
        run = self._run()
        self.assertFalse(dispatch_git_run(run))
        run.refresh_from_db()
        self.assertEqual(run.status, GitRun.SUCCESS)
