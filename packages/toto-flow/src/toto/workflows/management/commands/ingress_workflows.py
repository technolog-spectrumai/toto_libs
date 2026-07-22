from toto.ingress import IngressCommand


class Command(IngressCommand):
    help = "Verify the workflow engine is ready (no seed data required)."

    def process(self):
        from toto.workflows.models import LambdaFunction, Workflow

        lf_count = LambdaFunction.objects.count()
        wf_count = Workflow.objects.count()
        self.stdout.write(
            f"  [workflows] engine ready — {lf_count} lambda function(s), {wf_count} workflow(s)."
        )
