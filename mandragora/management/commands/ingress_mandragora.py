from django.contrib.auth.models import User
from django.core.management.base import CommandError

from mandragora.models import LambdaNode, LambdaLayer, LambdaUnitTest
from oya.ingress import IngressCommand


class Command(IngressCommand):
    help = "Creates a demo Mandragora setup with sample LambdaNodes and tests"

    def process(self):
        if not self.full:
            return

        # Ensure admin user exists
        user, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "demo@example.com"}
        )

        # Create a demo layer
        layer, _ = LambdaLayer.objects.get_or_create(
            name="DemoLayer",
            defaults={"use_opencv": False}
        )

        # Create demo nodes
        start_node = LambdaNode.objects.create(
            name="Start",
            layer=layer,
            code="""
def main(context):
    return {"message": "Workflow started"}
"""
        )

        process_node = LambdaNode.objects.create(
            name="Process",
            layer=layer,
            code="""
def main(context):
    return {"message": "Processing data..."}
"""
        )

        end_node = LambdaNode.objects.create(
            name="End",
            layer=layer,
            code="""
def main(context):
    return {"message": "Workflow finished"}
"""
        )

        # Create unit tests for each node
        LambdaUnitTest.objects.create(
            node=start_node,
            name="StartTest",
            input_context={},
            expected_output={"message": "Workflow started"},
        )

        LambdaUnitTest.objects.create(
            node=process_node,
            name="ProcessTest",
            input_context={},
            expected_output={"message": "Processing data..."},
        )

        LambdaUnitTest.objects.create(
            node=end_node,
            name="EndTest",
            input_context={},
            expected_output={"message": "Workflow finished"},
        )

        self.stdout.write(self.style.SUCCESS("Demo LambdaNodes and tests created."))
