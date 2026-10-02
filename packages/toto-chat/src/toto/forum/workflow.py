"""The one-node workflow every forum cleanup is dispatched through.

Get-or-create with node self-heal, mirroring antivirus, texlab and steven: a
missing workflow row must never be a stuck state an operator has to diagnose.
Seeded by `ingress_forum` too, so the Workflows tab shows "Forum cleanup"
from the first deploy rather than from the first night.

Only imported where `toto.workflows` is installed (`dispatch.py` checks
first; `ingress_forum` asks before importing).
"""

CLEANUP_WORKFLOW_SLUG = "forum-cleanup"
CLEANUP_TASK_NAME = "forum_cleanup"


def ensure_cleanup_workflow():
    """Get-or-create the cleanup workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _ = Workflow.objects.get_or_create(
        slug=CLEANUP_WORKFLOW_SLUG,
        defaults={
            "name": "Forum cleanup",
            "description": (
                "Permanently remove forum messages, files and polls older "
                "than the retention period: the nightly run, the forum-wide "
                "button on /forum/cleanup/ and each room's own. Started only "
                "by the platform, never by hand here; each run's input names "
                "the cleanup records it finishes."),
        },
    )
    if not workflow.nodes.filter(
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=CLEANUP_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow,
            node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=CLEANUP_TASK_NAME,
            label="remove what is past retention",
        )
    return workflow
