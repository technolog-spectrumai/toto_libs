"""The one-node workflows vault jobs are dispatched through.

Get-or-create with node self-heal, mirroring antivirus, texlab and steven: a
missing workflow row must never be a stuck state an operator has to diagnose.
"""

REFRESH_WORKFLOW_SLUG = "vault-remote-refresh"
REFRESH_TASK_NAME = "vault_refresh_remote_bucket"

TRANSFER_WORKFLOW_SLUG = "vault-transfer"
TRANSFER_TASK_NAME = "vault_transfer_files"


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


def ensure_transfer_workflow():
    """Get-or-create the transfer workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=TRANSFER_WORKFLOW_SLUG,
        defaults={
            "name": "Bucket transfer",
            "description": ("Copy the selected files between buckets when at "
                            "least one end is not this host's disk."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=TRANSFER_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=TRANSFER_TASK_NAME,
            label="copy the files",
        )
    return workflow
