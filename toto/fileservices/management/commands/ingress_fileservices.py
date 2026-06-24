from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Seed the generic 'fileservices-run' workflow used to track file-service runs."

    def process(self):
        self._ensure_run_workflow()

    def _ensure_run_workflow(self):
        """Seed the single-node workflow that dispatch_run() wraps every
        FileServiceRun in (slug 'fileservices-run'). Each transcription / media
        service run then shows up as a tracked WorkflowRun. Functional
        infra — seeded regardless of FULL_INGRESS."""
        from toto.workflows.models import Workflow, WorkflowNode

        wf, created = Workflow.objects.get_or_create(
            slug="fileservices-run",
            defaults={
                "name": "File Service Run",
                "description": (
                    "Runs a single file service (transcription, ffmpeg/ffprobe) "
                    "over a vault file and saves the output back to the vault."
                ),
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run file service",
                task_name="fileservice_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("Created workflow: File Service Run"))
        elif not wf.nodes.filter(task_name="fileservice_run").exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Run file service",
                task_name="fileservice_run",
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.WARNING("Added missing node to 'fileservices-run' workflow"))
        else:
            self.stdout.write(self.style.WARNING("Workflow 'fileservices-run' already exists"))
