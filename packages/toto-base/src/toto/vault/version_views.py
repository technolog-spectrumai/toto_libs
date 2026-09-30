"""HTTP surface for versions and editing locks.

Kept out of ``views.py``, which is already 1,400 lines, and out of the three
editors, which must not each grow their own copy — that is how primula ended up
with a versioning scheme the other two never got.

Authorisation reuses what the vault already decides. There is no new gate: if a
caller may open the file in an editor, they may read its history; if they may
write it, they may cut a version. cyprian deliberately lets a team edit a wiki
page none of them owns, so an owner-only rule here would break the one app that
most needs the history.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _

from toto.vault import access
from django.views.decorators.http import require_POST

from . import locks, versions
from .models import FileVersion, VaultFile

#: 423 Locked — the honest status for "somebody else is editing this". A 403
#: would say the caller lacks a right they in fact hold, and a 409 would invite
#: a retry that cannot succeed until the holder leaves.
HTTP_LOCKED = 423


def _file_for(request, pk: int) -> VaultFile:
    """The file, if this user may work with it. 404 otherwise.

    404 rather than 403 for a file they cannot reach: the vault does not confirm
    the existence of other people's documents.
    """
    vault_file = get_object_or_404(VaultFile, pk=pk)
    if request.user.is_superuser:
        return vault_file
    # A file in a bucket kept to clearances (2026-09-30) is the holders' alone:
    # not its owner, no folder ACL, no public flag, no lending app opens its
    # history or its lock to anyone who holds none of the bucket's clearances.
    if access.bucket_hidden(request.user, vault_file):
        raise Http404("No such file.")
    if vault_file.owner_id == request.user.pk:
        return vault_file
    if request.user.is_staff:
        return vault_file
    directory = vault_file.directory
    if directory is not None and directory.user_can_access(request.user):
        return vault_file
    if vault_file.is_public:
        return vault_file
    # A file another app lends out — a cyprian project-wiki page is held by the
    # project lead and written by the team. The module docstring above always
    # promised this ("cyprian deliberately lets a team edit a wiki page none of
    # them owns, so an owner-only rule here would break the one app that most
    # needs the history"), but the four clauses above never asked, so a
    # collaborator could save through cyprian and still be refused the lock and
    # the history by these endpoints.
    if access.may_edit_via_app(request.user, vault_file):
        return vault_file
    raise Http404("No such file.")


def _lock_state(vault_file, user) -> dict:
    """What the editor needs to know about who is holding this, as plain data."""
    lock = locks.holder_of(vault_file)
    if lock is None:
        return {"locked": False, "mine": False, "holder": "",
                "heartbeat_seconds": locks.HEARTBEAT_SECONDS}
    mine = lock.holder_id == getattr(user, "pk", None)
    return {
        "locked": True,
        "mine": mine,
        "holder": lock.holder.get_username(),
        "expires_at": lock.expires_at.isoformat(),
        "heartbeat_seconds": locks.HEARTBEAT_SECONDS,
    }


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------

@login_required
@require_POST
def lock_acquire(request, pk: int):
    """Claim the file for editing, or be told who has it."""
    vault_file = _file_for(request, pk)
    try:
        locks.acquire(vault_file, request.user)
    except locks.Locked as held:
        return JsonResponse(
            {"error": _("%(who)s is editing this document.")
                      % {"who": held.lock.holder},
             **_lock_state(vault_file, request.user)},
            status=HTTP_LOCKED)
    return JsonResponse(_lock_state(vault_file, request.user))


@login_required
@require_POST
def lock_heartbeat(request, pk: int):
    """Keep the lock alive. ``held: false`` means the caller lost it.

    Losing it is not an error — it is how an editor that went to sleep finds
    out, on its next beat, that the document moved on without it.
    """
    vault_file = _file_for(request, pk)
    held = locks.heartbeat(vault_file, request.user)
    return JsonResponse({"held": held, **_lock_state(vault_file, request.user)})


@login_required
@require_POST
def lock_release(request, pk: int):
    """Give the lock up. Sent by ``navigator.sendBeacon`` on page hide.

    Always 200, even when the caller held nothing: a beacon has nobody to report
    an error to, and expiry is the backstop that makes losing this harmless.
    """
    vault_file = _file_for(request, pk)
    locks.release(vault_file, request.user)
    return JsonResponse({"released": True})


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

def _version_json(version) -> dict:
    return {
        "id": version.pk,
        "number": version.number,
        "label": str(version.label),
        "author": version.author.get_username() if version.author_id else "",
        "created_at": version.created_at.isoformat(),
        "is_conflict": version.is_conflict,
        "pinned": version.is_pinned,
        "size_bytes": version.blob.size_bytes,
    }


@login_required
def version_list(request, pk: int):
    """This file's history, newest first."""
    vault_file = _file_for(request, pk)
    return JsonResponse({
        "versions": [_version_json(v) for v in versions.list_versions(vault_file)],
        **_lock_state(vault_file, request.user),
    })


@login_required
@require_POST
def version_save(request, pk: int):
    """Cut a version of whatever is in the file right now.

    The conscious act the whole design turns on. Autosave has already written
    the bytes; this records that somebody decided that state was worth keeping,
    and what they wanted to call it.
    """
    vault_file = _file_for(request, pk)
    if not access.is_local_content(vault_file):
        return JsonResponse(
            {"error": _("This file's bytes live on remote storage — "
                        "versions are cut and restored on the host that "
                        "holds them.")}, status=403)
    if not locks.may_write(vault_file, request.user):
        lock = locks.holder_of(vault_file)
        return JsonResponse(
            {"error": _("%(who)s is editing this document.") % {"who": lock.holder}},
            status=HTTP_LOCKED)

    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = {}
    label = (payload.get("label") or request.POST.get("label") or "").strip()

    version = versions.save_version(vault_file, author=request.user, label=label)
    return JsonResponse({"saved": True, **_version_json(version)})


@login_required
@require_POST
def version_restore(request, pk: int, version_pk: int):
    """Put an old body back, and record that as the newest version.

    Forward, never backward: restoring v3 onto a file at v7 produces v8. What
    happened between is not erased — that is the difference between a history
    and a current state.
    """
    vault_file = _file_for(request, pk)
    if not access.is_local_content(vault_file):
        return JsonResponse(
            {"error": _("This file's bytes live on remote storage — "
                        "versions are cut and restored on the host that "
                        "holds them.")}, status=403)
    if not locks.may_write(vault_file, request.user):
        lock = locks.holder_of(vault_file)
        return JsonResponse(
            {"error": _("%(who)s is editing this document.") % {"who": lock.holder}},
            status=HTTP_LOCKED)

    version = get_object_or_404(FileVersion, pk=version_pk, file=vault_file)

    # Screen on the way back in. These bytes were stored once, but "we accepted
    # it before" is not a verdict — a rule added since, or a file that predates
    # screening entirely, both land here.
    from toto.vault import scanning

    if scanning.should_scan(vault_file.owner, vault_file.file_type,
                            door="restore"):
        verdict = scanning.scan(version.read(), file_type=vault_file.file_type,
                                filename=vault_file.title)
        if not verdict.ok:
            scanning.record(vault_file, verdict, user=request.user,
                            door="restore")
            return JsonResponse(verdict.as_error(), status=400)
    else:
        verdict = None

    restored = versions.restore_version(version, actor=request.user)
    if verdict is not None:
        scanning.record(vault_file, verdict, user=request.user, door="restore")
    return JsonResponse({"restored": True, "from": version.number,
                         **_version_json(restored)})
