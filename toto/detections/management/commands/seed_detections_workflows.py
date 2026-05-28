"""
Seeds detections layer-export workflows in the workflow engine.
Run once after initial deployment or when resetting the database.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed detections layer-export workflows in the workflow engine."

    def handle(self, *args, **options):
        from toto.detections.workflows import (
            LAYER_EXPORT_DETECTIONS_ACTIVE_SLUG,
            LAYER_EXPORT_DETECTIONS_SEVERITY_SLUG,
            LAYER_EXPORT_DETECTIONS_ACTIVE_LAMBDA,
            LAYER_EXPORT_DETECTIONS_SEVERITY_LAMBDA,
        )

        self._seed_workflow(
            slug=LAYER_EXPORT_DETECTIONS_ACTIVE_SLUG,
            name="Layer Export — Detections Active",
            description="Rebuilds the active-detections MapLayer with per-address active detection counts.",
            lambda_name="layer_export_detections_active",
            lambda_content=LAYER_EXPORT_DETECTIONS_ACTIVE_LAMBDA,
        )
        self._seed_workflow(
            slug=LAYER_EXPORT_DETECTIONS_SEVERITY_SLUG,
            name="Layer Export — Detections Severity",
            description="Rebuilds the detection-severity MapLayer with average severity scores per address.",
            lambda_name="layer_export_detections_severity",
            lambda_content=LAYER_EXPORT_DETECTIONS_SEVERITY_LAMBDA,
        )
        self.stdout.write(self.style.SUCCESS("Detections workflows seeded."))

    def _seed_workflow(self, *, slug, name, description, lambda_name, lambda_content):
        from toto.workflows.models import LambdaFunction, Workflow, WorkflowNode

        fn, fn_created = LambdaFunction.objects.update_or_create(
            function_name=lambda_name,
            defaults={"content": lambda_content},
        )
        self.stdout.write(f"  {'Created' if fn_created else 'Updated'} lambda: {lambda_name}")

        wf, wf_created = Workflow.objects.update_or_create(
            slug=slug,
            defaults={"name": name, "description": description},
        )
        self.stdout.write(f"  {'Created' if wf_created else 'Workflow exists:'} {slug}")

        if not wf.nodes.filter(node_type=WorkflowNode.LAMBDA).exists():
            WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.LAMBDA,
                label=name,
                lambda_function=fn,
            )
            self.stdout.write(f"  Created lambda node for: {slug}")
        else:
            node = wf.nodes.filter(node_type=WorkflowNode.LAMBDA).first()
            node.lambda_function = fn
            node.save(update_fields=["lambda_function"])
            self.stdout.write(f"  Lambda node already exists for: {slug}")
