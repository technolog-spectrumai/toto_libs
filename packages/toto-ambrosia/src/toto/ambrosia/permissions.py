"""Who may open a workspace, and who may run code in one.

These are two different questions and the second is the serious one. Editing a
file is ordinary vault access. Running code is not: by decision this platform
runs workspace code as the server, with the ORM, SECRET_KEY, database
credentials and the media volume all reachable. So execution is gated on a
deliberate grant rather than on merely owning a workspace.
"""

from __future__ import annotations

from django.conf import settings

# Who may execute. "staff" is the safe default: it matches the existing posture
# for workflow lambdas, which are the same privilege in a less inviting wrapper.
#   "staff"        — is_staff or is_superuser         (default)
#   "superuser"    — superusers only
#   "authenticated"— anyone with an account. Only sane on a single-tenant host.
EXECUTION_ACCESS = getattr(settings, "AMBROSIA_EXECUTION_ACCESS", "staff")


def owns(user, workspace) -> bool:
    return bool(user and user.is_authenticated
                and workspace.owner_id == user.id)


def can_view(user, workspace) -> bool:
    """Owner, or staff looking at somebody else's workspace."""
    if not user or not user.is_authenticated:
        return False
    return owns(user, workspace) or user.is_staff


def can_edit(user, workspace) -> bool:
    """Only the owner edits. Staff may look without touching."""
    return owns(user, workspace)


def can_execute(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if EXECUTION_ACCESS == "authenticated":
        return True
    if EXECUTION_ACCESS == "superuser":
        return bool(user.is_superuser)
    return bool(user.is_staff or user.is_superuser)


def execution_refusal() -> str:
    """Why running is refused, in words that say what to do about it."""
    return (
        "Running code in a workspace is restricted on this platform. Workspace "
        "code executes with the server's own privileges, so an administrator has "
        "to grant it. Ask one to give you staff access, or set "
        "AMBROSIA_EXECUTION_ACCESS if this is your own deployment."
    )
