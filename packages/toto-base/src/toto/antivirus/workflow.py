"""The one-node workflow a scan is dispatched through.

Get-or-create with node self-heal, mirroring texlab, aralia and steven: a
missing workflow row must never be a stuck state an operator has to diagnose.
"""

SCAN_WORKFLOW_SLUG = "antivirus-scan"
SCAN_TASK_NAME = "antivirus_scan"


def ensure_scan_workflow():
    """Get-or-create the scan workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=SCAN_WORKFLOW_SLUG,
        defaults={
            "name": "Antivirus scan",
            "description": ("Read one vault file, screen its content, and "
                            "record the verdict."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=SCAN_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=SCAN_TASK_NAME,
            label="scan the file",
        )
    return workflow
