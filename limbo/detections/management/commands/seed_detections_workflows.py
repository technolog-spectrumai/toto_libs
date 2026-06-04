"""Seeds the detections export workflow in the workflow engine."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed detections export workflow in the workflow engine."

    def handle(self, *args, **options):
        from toto.workflows.models import Workflow, WorkflowNode
        from toto.detections.predefined_tasks import DETECTIONS_EXPORT_SLUG, DETECTIONS_EXPORT_TASK

        wf, created = Workflow.objects.get_or_create(
            slug=DETECTIONS_EXPORT_SLUG,
            defaults={
                "name": "Detections — Export Layers",
                "description": "Exports active detection map layers to a Vault file.",
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Export detection layers to Vault",
                task_name=DETECTIONS_EXPORT_TASK,
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS("Detections export workflow seeded."))
        else:
            if not wf.nodes.filter(task_name=DETECTIONS_EXPORT_TASK).exists():
                WorkflowNode.objects.create(
                    workflow=wf,
                    node_type=WorkflowNode.PREDEFINED_TASK,
                    label="Export detection layers to Vault",
                    task_name=DETECTIONS_EXPORT_TASK,
                    position_x=0,
                    position_y=0,
                )
                self.stdout.write(self.style.SUCCESS("  Added missing node to detection export workflow."))
            else:
                self.stdout.write("  Detections export workflow already seeded.")
