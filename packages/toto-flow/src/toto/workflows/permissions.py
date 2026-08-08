"""Who may do what to a workflow.

Ownership arrived late (migration 0003): a NULL owner marks a SYSTEM workflow
— seeded by ingress or self-healed by an app — and those are staff-managed.
Viewing and *running* stay open to every signed-in user: a run is metered and
charged to whoever starts it, so the gate on runs is economic, not access.
"""

from __future__ import annotations


def can_manage_workflow(user, workflow) -> bool:
    """Edit/delete/rewire. Owner or staff; system workflows staff-only."""
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff:
        return True
    return workflow.owner_id is not None and workflow.owner_id == user.pk


def can_cancel_run(user, run) -> bool:
    """The run's starter, the workflow's owner, or staff."""
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        user.is_staff
        or (run.started_by_id is not None and run.started_by_id == user.pk)
        or (run.workflow.owner_id is not None and run.workflow.owner_id == user.pk)
    )
