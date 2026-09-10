"""The workspace API: browse, read, write and run, for a client with no session.

The room is moving to a desktop app, and an editor that cannot open a file is
not an editor. Anastasia's `/api/v1/` covers capsules and jobs; this covers the
half a person actually looks at — the tree, a file's text, and the two verbs
that make a workspace worth having.

Four rules, three of them borrowed on purpose.

**One vocabulary, not two.** Every view here calls the same `services` and
`filetree` functions the room calls, and returns the same shapes. A second
serialisation layer would be a second place for the truth to drift.

**A token is its owner.** `token_required` comes from `toto.anastasia.api`
rather than being reimplemented, so there is one credential and one place it is
checked. Permission is then decided by `permissions.can_view` / `can_edit` —
the same predicates the pages use, which conveniently take a USER and not a
request. A token therefore reaches exactly what its owner reaches in a browser:
no more, and no less.

**Somebody else's workspace is a 404, never a 403.** Telling a stranger they
may not see a workspace also tells them it exists.

**The refusal shape is anastasia's** — `{"error": …, "code": …}` — not the
room's `{"ok": false, "error": …}`. A client already switches on the first one
for every capsule and job call; making it handle a second shape for files would
be a cost paid by every caller for no gain. The room's own endpoints keep
theirs; they have a different, older client.

WHY THIS LIVES IN AMBROSIA AND NOT ANASTASIA
--------------------------------------------
Anastasia owns no documents and must not learn what a workspace is — that
boundary is the whole reason it is a separate distribution. So the workspace
API belongs on the workspace side, and it imports anastasia rather than the
reverse. `api_urls.py` is mounted only where anastasia is installed, which is
what keeps ambrosia's dependency on it soft: a host with no capsules imports
neither module.
"""

from __future__ import annotations

import json

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.views.decorators.http import require_GET, require_POST

from toto.anastasia.api import token_required

from . import filetree, permissions, registry, services
from .models import Workspace
from .views import _resolve_dir, _workspace_file

#: The version in the path, matching anastasia's. One client, one version
#: number: a desktop app that had to track two would eventually be built
#: against a pair nobody tested together.
VERSION = "v1"


def _error(message: str, *, code: str = "", status: int = 400):
    return JsonResponse({"error": message, "code": code}, status=status)


def _refusal(exc):
    """A ValidationError from `services`, in the API's shape.

    409 rather than 400: these are refusals about the STATE of things — a name
    already taken, a file too large, a generated file that may not be edited —
    not malformed requests. The client shows the sentence.
    """
    message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
    return _error(message, code=getattr(exc, "refusal_code", ""), status=409)


def _body(request) -> dict:
    try:
        return json.loads(request.body or b"{}")
    except ValueError:
        return {}


def _own_workspace(owner, slug, *, for_edit=False) -> Workspace:
    """The workspace this token may act on, or a 404.

    Deliberately NOT `views._get_workspace`: that one takes a request, reads
    the URL namespace to decide which language app owns the room, and raises
    `WrongRoom`. None of that applies here — this API is not mounted under a
    language namespace and a client addresses a workspace by slug regardless of
    kind.

    What IS reused is the pair of predicates, so the rule cannot drift: reading
    is `can_view` (owner, or staff), writing is `can_edit` (owner only — there
    are no collaborators in ambrosia).
    """
    workspace = (Workspace.objects
                 .select_related("bucket", "root_directory", "owner")
                 .filter(slug=slug).first())
    if workspace is None:
        raise Http404("no such workspace")
    allowed = (permissions.can_edit(owner, workspace) if for_edit
               else permissions.can_view(owner, workspace))
    if not allowed:
        raise Http404("no such workspace")
    return workspace


def _workspace_json(workspace) -> dict:
    """What a client needs to list and open one.

    `namespace` rather than a boolean per language: it is the registry's own
    key, so a third language app appears here without this function changing.
    """
    app = registry.for_kind(workspace.kind)
    return {
        "slug": workspace.slug,
        "name": workspace.name,
        "kind": workspace.kind,
        "namespace": app.namespace if app is not None else "",
        "owner": workspace.owner.get_username(),
        # Whether THIS token may write, answered by the server. A client that
        # decided for itself would have to know the collaborator rules, and
        # would be wrong the day they change.
        "writable": False,
        "created_at": workspace.created_at,
    }


# --------------------------------------------------------------------------- #
# Workspaces                                                                   #
# --------------------------------------------------------------------------- #

@require_GET
@token_required
def workspace_list(request, owner):
    """The workspaces this person owns.

    Owned, not merely viewable: a staff user may open somebody else's room by
    slug, but listing every workspace on the host as though they were theirs
    would make the client's "my workspaces" screen a lie.
    """
    rows = (Workspace.objects.filter(owner=owner)
            .select_related("owner").order_by("name"))
    return JsonResponse({"workspaces": [
        {**_workspace_json(w), "writable": True} for w in rows]})


@require_GET
@token_required
def workspace_detail(request, owner, slug):
    workspace = _own_workspace(owner, slug)
    return JsonResponse({
        **_workspace_json(workspace),
        "writable": permissions.can_edit(owner, workspace),
        "can_execute": permissions.can_execute(owner),
    })


@require_GET
@token_required
def workspace_tree(request, owner, slug):
    """The whole tree, flat and depth-tagged — `filetree.flatten`'s own shape.

    NOT paginated, and that is a decision rather than an omission: the tree is
    one bucket's worth of rows, the room already renders all of them, and a
    client that had to page would have to reassemble the hierarchy itself.
    """
    workspace = _own_workspace(owner, slug)
    return JsonResponse({"items": filetree.flatten(workspace)})


# --------------------------------------------------------------------------- #
# Files                                                                        #
# --------------------------------------------------------------------------- #

@require_GET
@token_required
def file_read(request, owner, slug, pk):
    """One file's text.

    `truncated` is load-bearing and must not be dropped by a client: a file
    over the read ceiling comes back SHORTENED, and saving that back would
    destroy the tail. The room greys the editor out when it is true.
    """
    workspace = _own_workspace(owner, slug)
    vault_file = _workspace_file(workspace, pk)
    try:
        content, truncated = services.read_file(vault_file)
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse({
        "pk": vault_file.pk,
        "name": vault_file.title,
        "file_type": vault_file.file_type,
        "content": content,
        "truncated": truncated,
        "readonly": filetree.is_artifact(vault_file),
        "size": vault_file.file_size_bytes,
    })


@require_POST
@token_required
def file_write(request, owner, slug, pk):
    """Save text back.

    Goes through `services.write_file`, which is where the vault's own rules
    live — size, type, the antivirus door and the storage levy's byte count.
    Writing the model directly from here would bypass every one of them.
    """
    workspace = _own_workspace(owner, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    if filetree.is_artifact(vault_file):
        # Enforced here and not only in the tree's greying: a hand-made call
        # must not overwrite a compile's output either, and the next run would
        # discard the edit regardless.
        return _error("Generated files are read-only — edit the source "
                      "instead.", code="generated_file", status=409)
    try:
        services.write_file(vault_file=vault_file,
                            content=_body(request).get("content", ""))
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse({"pk": vault_file.pk,
                         "size": vault_file.file_size_bytes})


@require_POST
@token_required
def file_create(request, owner, slug):
    workspace = _own_workspace(owner, slug, for_edit=True)
    payload = _body(request)
    directory = _resolve_dir(workspace, payload.get("directory"))
    try:
        vault_file = services.create_file(
            workspace=workspace, user=owner,
            filename=payload.get("name", ""), directory=directory)
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse({"pk": vault_file.pk, "name": vault_file.title,
                         "items": filetree.flatten(workspace)}, status=201)


@require_POST
@token_required
def dir_create(request, owner, slug):
    workspace = _own_workspace(owner, slug, for_edit=True)
    payload = _body(request)
    parent = _resolve_dir(workspace, payload.get("parent"))
    try:
        services.create_directory(workspace=workspace, user=owner,
                                  name=payload.get("name", ""), parent=parent)
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse({"items": filetree.flatten(workspace)}, status=201)


@require_POST
@token_required
def file_rename(request, owner, slug, pk):
    workspace = _own_workspace(owner, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    try:
        services.rename_file(workspace=workspace, vault_file=vault_file,
                             name=_body(request).get("name", ""))
    except ValidationError as exc:
        return _refusal(exc)
    return JsonResponse({"items": filetree.flatten(workspace)})


@require_POST
@token_required
def file_delete(request, owner, slug, pk):
    workspace = _own_workspace(owner, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    services.delete_file(workspace=workspace, vault_file=vault_file)
    return JsonResponse({"items": filetree.flatten(workspace)})


# --------------------------------------------------------------------------- #
# Running                                                                      #
# --------------------------------------------------------------------------- #

@require_POST
@token_required
def workspace_run(request, owner, slug):
    """Do whatever this workspace's language does. ONE endpoint, not two.

    A Python workspace runs its code; a LaTeX workspace queues a compile. The
    client sends its body and reads the answer — it does not have to know which
    kind it is holding, and a third language would appear here without this
    function changing or the client being rebuilt.

    The two shapes are genuinely different and are NOT flattened into a common
    one: Python answers synchronously with output, LaTeX answers with a run to
    poll. Inventing a shared envelope would mean every caller unwrapping a
    fiction. `kind` in the workspace detail is how a client knows which to
    expect, and `queued` in the reply is how it knows without asking.

    `for_edit=True`: running is a write. It creates files in the bucket, spends
    the owner's quota and charges them — a read-only viewer must not.
    """
    workspace = _own_workspace(owner, slug, for_edit=True)
    app = registry.for_kind(workspace.kind)
    if app is None or app.run is None:
        # 404, not 405: from the client's side this workspace has no such
        # verb, and there is nothing it could send to make one appear.
        return _error("this workspace has nothing to run",
                      code="no_run_verb", status=404)
    try:
        result = app.run(workspace, user=owner, payload=_body(request))
    except registry.RunRefused as exc:
        return _error(exc.message, code=exc.code, status=exc.status)
    return JsonResponse({
        "slug": workspace.slug,
        "kind": workspace.kind,
        # Whether the answer above IS the answer, or a receipt to poll. Set
        # from the payload rather than from the kind, so a language that later
        # changes which way it works does not need a client change.
        "queued": bool(result.get("status") == "queued"),
        **result,
    })
