from django.utils import timezone
from oya.ingress import IngressCommand
from django.contrib.auth.models import User
from mandragora.models import Workflow, FunctionNode, AlgoNode, Edge


class Command(IngressCommand):
    help = "Creates a demo workflow with FunctionNode and AlgoNode connected by edges"

    def process(self, _):
        self.create_dashboard_item(
            title="Workflow Engine",
            icon="project-diagram",
            description="A demo workflow with executable nodes and edges.",
            link="/workflow/"
        )

        # Create demo user (if needed for ownership)
        user, _ = User.objects.get_or_create(
            username='workflow_demo',
            defaults={'email': 'workflow@example.com'}
        )

        # Create workflow
        workflow = Workflow.objects.create(
            name="Demo Workflow",
            description="A sample workflow with connected nodes",
            created_at=timezone.now()
        )

        # Create nodes
        fn_node = FunctionNode.objects.create(
            workflow=workflow,
            name="Function Step",
            code="""
def run(input_data):
    return f"Processed: {input_data}"
""".strip()
        )

        algo_node = AlgoNode.objects.create(
            workflow=workflow,
            name="Algorithm Step"
        )

        # Connect nodes with an edge
        Edge.objects.create(
            workflow=workflow,
            source=fn_node,
            target=algo_node
        )

        self.stdout.write(self.style.SUCCESS("Demo workflow with nodes and edge created successfully!"))
