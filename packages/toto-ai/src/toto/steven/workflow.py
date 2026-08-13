"""The one-node workflow an ask is dispatched through.

Get-or-create with node self-heal, mirroring aralia and texlab: "the workflow row
is missing" must never be a stuck state an operator has to diagnose. If somebody
deletes it, the next ask rebuilds it.
"""

ASK_WORKFLOW_SLUG = "steven-ask"
ASK_TASK_NAME = "steven_ask"


def ensure_ask_workflow():
    """Get-or-create the ask workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=ASK_WORKFLOW_SLUG,
        defaults={
            "name": "Steven ask",
            "description": (
                "Send one selection to the configured AI provider and record "
                "the answer and its token count."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=ASK_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=ASK_TASK_NAME,
            label="ask the model",
        )
    return workflow
