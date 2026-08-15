"""The one-node workflows vault jobs are dispatched through.

Get-or-create with node self-heal, mirroring antivirus, texlab and steven: a
missing workflow row must never be a stuck state an operator has to diagnose.
"""

REFRESH_WORKFLOW_SLUG = "vault-remote-refresh"
REFRESH_TASK_NAME = "vault_refresh_remote_bucket"


def ensure_refresh_workflow():
    """Get-or-create the mirror-refresh workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=REFRESH_WORKFLOW_SLUG,
        defaults={
            "name": "Remote bucket refresh",
            "description": ("Walk a paired host's bucket listing and bring "
                            "the local metadata stubs up to date."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=REFRESH_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=REFRESH_TASK_NAME,
            label="refresh the mirror",
        )
    return workflow
