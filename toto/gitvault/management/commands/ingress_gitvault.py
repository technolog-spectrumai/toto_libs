from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed the 'gitvault-run' workflow used to track git init/push/pull runs."

    def process(self):
        self._ensure_run_workflow()

    def _ensure_run_workflow(self):
        """Seed the single-node workflow that dispatch_git_run() wraps every
        GitRun in (slug 'gitvault-run'). Each git init/push/pull then shows up
        as a tracked WorkflowRun in the workflows UI. Functional infra —
        seeded regardless of FULL_INGRESS."""
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="gitvault-run",
            defaults={
                "name": "Git Vault Run",
                "description": (
                    "Runs a background git operation (init/push/pull) on a "
                    "git-enabled vault directory, syncing the working tree "
                    "with the vault and the co-deployed Gitea remote."
                ),
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run git operation",
                task_name="gitvault_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("Created workflow: Git Vault Run"))
        elif not wf.nodes.filter(task_name="gitvault_run").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run git operation",
                task_name="gitvault_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.WARNING("Added missing node to 'gitvault-run' workflow"))
        else:
            self.stdout.write(self.style.WARNING("Workflow 'gitvault-run' already exists"))
