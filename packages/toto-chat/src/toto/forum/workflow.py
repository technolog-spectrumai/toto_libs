"""The one-node workflow every forum cleanup is dispatched through.

Get-or-create with node self-heal, as the antivirus has it: a missing
workflow row must never be a stuck state an operator has to diagnose.
Made by ``ingress_forum`` too, so the Workflows tab shows "Forum cleanup"
from the first deploy rather than from the first night.

Only imported where ``toto.workflows`` is installed (``dispatch.py`` checks
first; ``ingress_forum`` asks before importing).
"""

CLEANUP_WORKFLOW_SLUG = "forum-cleanup"
CLEANUP_TASK_NAME = "forum_cleanup"


def ensure_cleanup_workflow():
    """Get-or-create the cleanup workflow. Idempotent."""
    from toto.workflows.models import Workflow, WorkflowNode

    workflow, _created = Workflow.objects.get_or_create(
        slug=CLEANUP_WORKFLOW_SLUG,
        defaults={
            "name": "Forum cleanup",
            "description": (
                "Permanently remove forum messages, their images and polls "
                "older than a boundary: the nightly run at the retention age, "
                "and each cleanup an administrator starts on the forum's "
                "Settings page. Started only by the platform, never by hand "
                "here; each run's input names the cleanup records it finishes."),
        },
    )
    if not workflow.nodes.filter(node_type=WorkflowNode.PREDEFINED_TASK,
                                 task_name=CLEANUP_TASK_NAME).exists():
        WorkflowNode.objects.create(
            workflow=workflow, node_type=WorkflowNode.PREDEFINED_TASK,
            task_name=CLEANUP_TASK_NAME, label="remove what is past the boundary")
    return workflow
