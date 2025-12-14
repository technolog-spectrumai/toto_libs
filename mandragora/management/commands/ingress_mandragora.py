from django.contrib.auth.models import User
from mandragora.models import Workflow, LambdaNode, Edge, LambdaLayer
from oya.models import Platform, Theme
from oya.ingress import IngressCommand



class Command(IngressCommand):
    help = "Creates a demo Mandragora workflow setup with sample nodes and edges"

    def process(self):
        if not self.full:
            return

        # Use existing user or create demo
        user, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "demo@example.com"}
        )

        # Create demo workflow
        workflow, created = Workflow.objects.get_or_create(
            name="Demo Workflow",
            defaults={
                "description": "A sample workflow for Mandragora demo",
                "timeout": 60,
                "metadata": {"demo": True, "owner": user.username},
            }
        )

        if not created:
            # Avoid duplicating nodes/edges if workflow already exists
            return

        kanban_layer, created = LambdaLayer.objects.get_or_create(
            name="KanbanLayer",
        )

        # Create nodes with enforced main() function
        start_node = LambdaNode.objects.create(
            workflow=workflow,
            name="Start",
            is_initial=True,
            layer=kanban_layer,
            code="""
def main(context):
    return {"message": "Workflow started"}
"""
        )
        process_node = LambdaNode.objects.create(
            workflow=workflow,
            name="Process",
            code="""
def main(context):
    return {"message": "Processing data..."}
"""
        )
        end_node = LambdaNode.objects.create(
            workflow=workflow,
            name="End",
            is_final=True,
            code="""
def main(context):
    return {"message": "Workflow finished"}
"""
        )

        # Create edges (transitions)
        Edge.objects.create(
            workflow=workflow,
            source=start_node,
            target=process_node,
            action="next"
        )
        Edge.objects.create(
            workflow=workflow,
            source=process_node,
            target=end_node,
            action="complete"
        )
