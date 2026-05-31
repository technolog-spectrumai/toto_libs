"""Seeds the logistics fleet-export workflow into the workflow engine."""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed logistics workflows (fleet GeoJSON export) in the workflow engine."

    def handle(self, *args, **options):
        from toto.workflows.models import Workflow, WorkflowNode
        from toto.logistics.predefined_tasks import FLEET_EXPORT_SLUG, FLEET_EXPORT_TASK

        wf, created = Workflow.objects.get_or_create(
            slug=FLEET_EXPORT_SLUG,
            defaults={
                "name": "Logistics — Fleet Export",
                "description": "Exports mobile fleet objects (inventory) as a GeoJSON FeatureCollection to a Vault file.",
            },
        )
        if created:
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.PREDEFINED_TASK,
                label="Export fleet GeoJSON to Vault",
                task_name=FLEET_EXPORT_TASK,
                position_x=0,
                position_y=0,
            )
            self.stdout.write(self.style.SUCCESS(f"Created workflow: {FLEET_EXPORT_SLUG}"))
        else:
            if not wf.nodes.filter(task_name=FLEET_EXPORT_TASK).exists():
                WorkflowNode.objects.create(
                    workflow=wf,
                    node_type=WorkflowNode.PREDEFINED_TASK,
                    label="Export fleet GeoJSON to Vault",
                    task_name=FLEET_EXPORT_TASK,
                    position_x=0,
                    position_y=0,
                )
                self.stdout.write(f"Added task node to existing workflow: {FLEET_EXPORT_SLUG}")
            else:
                self.stdout.write(self.style.WARNING(f"Workflow already seeded: {FLEET_EXPORT_SLUG}"))
