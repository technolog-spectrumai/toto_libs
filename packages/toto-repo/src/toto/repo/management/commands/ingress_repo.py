from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed the 'repo-run' workflow used to track git init/push/pull runs."

    def process(self):
        self._ensure_run_workflow()

    def _ensure_run_workflow(self):
        """Seed the single-node workflow that dispatch_git_run() wraps every
        GitRun in (slug 'repo-run'). Each git init/push/pull then shows up
        as a tracked WorkflowRun in the workflows UI. Functional infra —
        seeded regardless of FULL_INGRESS."""
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="repo-run",
            defaults={
                "name": "Repository run",
                "description": (
                    "Runs a background git operation (init/push/pull) on a "
                    "git-enabled vault directory, syncing the working tree "
                    "with the vault and, when one is set, the remote."
                ),
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run git operation",
                task_name="repo_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("Created workflow: Repository run"))
        elif not wf.nodes.filter(task_name="repo_run").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run git operation",
                task_name="repo_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.WARNING("Added missing node to 'repo-run' workflow"))
        else:
            self.stdout.write(self.style.WARNING("Workflow 'repo-run' already exists"))
