"""The lobby, the workspace room, and the small JSON endpoints behind it.

Function-based with `@login_required`, the local convention. Every page render
goes through `PageProcessor` or the base template comes out unstyled.

The room ships the file TREE (names and types) but no file CONTENT — content
arrives over `file_content` when a tab is opened, so a workspace with a hundred
files does not inline a hundred files into the HTML.

Since the split these views serve under TWO namespaces — `dracena` and
`texlab` mount the same shared urlpatterns — so nothing here reverses a
hardcoded namespace: `_ns(request)` reads the live one off the resolver match,
and the registry supplies whatever the workspace's kind adds to the room.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault.models import VaultDirectory, VaultFile

from . import (filetree, hibernation, permissions, registry, services,
               settings_spec)
from .forms import DestroyWorkspaceForm, WorkspaceCreateForm
from .models import Workspace, WorkspaceKind


def _ns(request) -> str:
    """The live URL namespace — `dracena` or `texlab`.

    Both apps mount the same shared urlpatterns, so a view cannot hardcode a
    namespace; the resolver match is the one source that is always right.
    """
    return request.resolver_match.namespace


def _rev(request, name, *args) -> str:
    return reverse(f"{_ns(request)}:{name}", args=args)


class WrongRoom(Http404):
    """A workspace opened under the other language's namespace."""

    def __init__(self, workspace):
        super().__init__("No such workspace")
        self.workspace = workspace


def _get_workspace(request, slug, *, for_edit=False) -> Workspace:
    workspace = get_object_or_404(
        Workspace.objects.select_related("bucket", "root_directory", "owner"),
        slug=slug)
    app = registry.for_namespace(_ns(request))
    if app is not None and workspace.kind != app.kind:
        # A python workspace has no business in the texlab room and vice
        # versa. The HTML room view turns this into a redirect to the right
        # namespace; JSON endpoints let it surface as the 404 it subclasses.
        raise WrongRoom(workspace)
    if for_edit:
        if not permissions.can_edit(request.user, workspace):
            raise Http404("No such workspace")
    elif not permissions.can_view(request.user, workspace):
        # 404 rather than 403: telling somebody they may not see a workspace
        # also tells them it exists.
        raise Http404("No such workspace")
    return workspace


def _workspace_file(workspace, pk) -> VaultFile:
    """A file inside this workspace's BUCKET.

    The bucket is the boundary — see filetree.py's header for why that replaced
    folder scoping. `_get_workspace` has already proved the workspace is yours,
    and a workspace belongs to exactly one bucket, so "in this bucket" is a
    complete answer to "may I touch this file". Nothing outside it resolves.
    """
    return get_object_or_404(VaultFile, pk=pk, bucket_id=workspace.bucket_id)


def _ok(**payload):
    return JsonResponse({"ok": True, **payload})


def _fail(message, status=400):
    return JsonResponse({"ok": False, "error": message}, status=status)


def _body(request) -> dict:
    try:
        return json.loads(request.body or "{}")
    except ValueError:
        return {}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _directory_map(user) -> dict:
    """Folders grouped by bucket, so the form's second select can filter.

    A JSON island rather than a round trip on every bucket change — the whole
    list is a few dozen rows and the form is one page.

    Returned as a dict, NOT as a JSON string: `json_script` serialises what it
    is given, so handing it something already serialised produces a quoted
    string that `JSON.parse` unwraps back into a string. The template then has
    an island the browser parses successfully and cannot use.
    """
    from toto.vault.models import VaultDirectory

    out: dict[str, list] = {}
    rows = (VaultDirectory.objects.filter(bucket__owner=user)
            .select_related("bucket").order_by("bucket__name", "name"))
    for directory in rows:
        out.setdefault(str(directory.bucket_id), []).append(
            {"id": directory.pk, "label": directory.full_path()})
    return out


@login_required
def lobby(request):
    app = registry.for_namespace(_ns(request))
    workspaces = (Workspace.objects
                  .filter(owner=request.user)
                  .select_related("bucket", "root_directory"))
    if app is not None:
        workspaces = workspaces.filter(kind=app.kind)
    buckets = services.buckets_for(request.user)
    context = {
        "ns": _ns(request),
        "kind": app.kind if app else "",
        "workspaces": workspaces,
        "form": WorkspaceCreateForm(user=request.user,
                                    kind=app.kind if app else None),
        "has_buckets": buckets.exists(),
        "directory_map": _directory_map(request.user),
        "can_execute": permissions.can_execute(request.user),
        "execution_refusal": permissions.execution_refusal(),
    }
    return render(request, "ambrosia/lobby.html",
                  PageProcessor().decorate(context, request))


@login_required
@require_POST
def workspace_create(request):
    app = registry.for_namespace(_ns(request))
    form = WorkspaceCreateForm(request.POST, user=request.user,
                               kind=app.kind if app else None)
    if not form.is_valid():
        messages.error(request, "; ".join(
            f"{field}: {msg}" if field != "__all__" else msg
            for field, errors in form.errors.items() for msg in errors))
        return redirect(_rev(request, "lobby"))
    try:
        workspace = services.create_workspace(
            owner=request.user,
            name=form.cleaned_data["name"],
            bucket=form.cleaned_data["bucket"],
            directory=form.cleaned_data.get("directory"),
            new_directory_name=form.cleaned_data.get("new_directory_name", ""),
            kind=(app.kind if app else
                  form.cleaned_data.get("kind") or WorkspaceKind.PYTHON),
        )
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(_rev(request, "lobby"))
    messages.success(request, f"{workspace.name} is ready in {workspace.path_label}.")
    return redirect(_rev(request, "workspace", workspace.slug))


@login_required
def workspace(request, slug):
    try:
        workspace = _get_workspace(request, slug)
    except WrongRoom as wrong:
        # Opened under the other language's namespace — send the browser to
        # the right room rather than lying that the workspace does not exist.
        other = registry.for_kind(wrong.workspace.kind)
        if other is None:
            raise Http404("No such workspace") from wrong
        return redirect(
            reverse(f"{other.namespace}:workspace", args=[slug]), permanent=True)
    workspace.touch()

    app = registry.for_namespace(_ns(request))
    extra = app.extra_context(request, workspace) if app else {}

    config_urls = {
        "tree": _rev(request, "tree", workspace.slug),
        "content": _rev(request, "file_content", workspace.slug, 0),
        "save": _rev(request, "file_save", workspace.slug, 0),
        "createFile": _rev(request, "file_create", workspace.slug),
        "createDir": _rev(request, "dir_create", workspace.slug),
        "rename": _rev(request, "file_rename", workspace.slug, 0),
        "delete": _rev(request, "file_delete", workspace.slug, 0),
        "raw": _rev(request, "file_raw", workspace.slug, 0),
        "settings": _rev(request, "workspace_settings", workspace.slug),
    }
    if app:
        config_urls.update(app.extra_urls(_ns(request), workspace))

    # The settings panel: what this kind offers, and what is in force. Both are
    # plain data from the lab's own declaration — the base names no key.
    settings_fields = app.settings_fields() if app else ()
    settings_values = settings_spec.effective(
        settings_fields, workspace.settings_for(_ns(request)), workspace=workspace)

    context = {
        "ns": _ns(request),
        "workspace": workspace,
        # The registry supplies the kind-specific half; these are the neutral
        # defaults a kindless build would render with.
        "latex_available": True,
        "worker_available": True,
        "destroy_form": DestroyWorkspaceForm(workspace=workspace),
        "readonly": not permissions.can_edit(request.user, workspace),
        "can_execute": permissions.can_execute(request.user),
        "execution_refusal": permissions.execution_refusal(),
        # The panel section this kind owns, or "" for a kind that offers none.
        "settings_template": app.settings_template if app else "",
        # …and the cards it adds to the room. A kind that adds none renders the
        # room exactly as before.
        "room_panels": app.room_panels if app else (),
        # Plain Python, not a JSON string — see _directory_map.
        "tree_data": filetree.flatten(workspace),
        "config_data": {
            "slug": workspace.slug,
            "kind": workspace.kind,
            "isLatex": workspace.is_latex,
            # The assistant, if this host has one. The workspace KIND picks the
            # surface: a .py open in a LaTeX project is in a LaTeX room, and a
            # prose vocabulary there would be the wrong tool.
            "stevenSurface": assistant.surface_for(
                "editor-latex" if workspace.is_latex else "editor-code"),
            "usesKernel": workspace.uses_kernel,
            "mainPk": extra.pop("mainPk", None),
            "canExecute": permissions.can_execute(request.user),
            "readonly": not permissions.can_edit(request.user, workspace),
            # ?destroy=1 opened the old bottom disclosure; it opens the drawer
            # that replaced it, so the bookmarked link still lands somewhere.
            "openSettings": request.GET.get("destroy") == "1",
            "settings": settings_values,
            "settingsFields": settings_spec.describe(
                settings_fields, workspace=workspace),
            "urls": config_urls,
        },
    }
    context.update(extra)
    context.update(_repo_context(workspace, request.user))
    return render(request, "ambrosia/workspace.html",
                  PageProcessor().decorate(context, request))


def _repo_context(workspace, user) -> dict:
    """Git toolbar context for the room, or {} where toto.repo is off.

    Anchored at ``workspace.root_directory`` — the same OneToOne unit a
    ``GitRepo`` keys on, and the same subtree Destroy deletes. Note the
    asymmetry with the explorer: the tree shows the whole BUCKET (a shared
    preamble.sty at the bucket root is editable here), but the repo covers only
    the workspace's own folder. Versioning follows ownership, not visibility.
    """
    from django.apps import apps as django_apps

    if not django_apps.is_installed("toto.repo"):
        return {}
    from toto.repo.integration import context_for_directory

    ctx = context_for_directory(workspace.root_directory, user)
    return {"repo_ctx": ctx} if ctx else {}


@login_required
@require_POST
def workspace_settings(request, slug):
    """Save (or reset) how this workspace runs.

    Shared by both labs — the route ships in `workspace_urlpatterns()`, so it
    exists under each namespace, and the namespace decides whose settings these
    are. `for_edit=True` means a non-owner gets 404 rather than 403, like every
    other mutation here.

    Execution-affecting keys need `can_execute` on top of ownership: the person
    who may open a room is not automatically the person who may change what its
    interpreter is allowed to do.
    """
    workspace = _get_workspace(request, slug, for_edit=True)
    body = _body(request)
    namespace = _ns(request)

    try:
        if body.get("reset"):
            values = services.reset_settings(
                workspace=workspace, namespace=namespace)
        else:
            values = services.update_settings(
                workspace=workspace,
                namespace=namespace,
                values=body.get("settings") or {},
                may_execute=permissions.can_execute(request.user),
            )
    except ValidationError as exc:
        # message_dict when the failure names fields, so the panel can put each
        # sentence under its own control; a bare list otherwise.
        if hasattr(exc, "error_dict"):
            return JsonResponse(
                {"ok": False, "error": "; ".join(exc.messages),
                 "fields": {key: [str(m) for m in msgs]
                            for key, msgs in exc.message_dict.items()}},
                status=409)
        return _fail("; ".join(exc.messages), status=409)

    app = registry.for_namespace(namespace)
    fields = app.settings_fields() if app else ()
    return _ok(settings=values,
               fields=settings_spec.describe(fields, workspace=workspace),
               restartRequired=any(f.restart_hint for f in fields
                                   if f.key in (body.get("settings") or {})))


@login_required
@require_POST
def workspace_close(request, slug):
    """Forget the workspace. The folder and every file stay in the vault."""
    workspace = _get_workspace(request, slug, for_edit=True)
    _teardown_quietly(workspace)
    try:
        name = services.close_workspace(workspace=workspace, user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(_rev(request, "workspace", slug))
    messages.info(request, f"{name} closed. Its files are still in the vault.")
    return redirect(_rev(request, "lobby"))


@login_required
@require_POST
def workspace_destroy(request, slug):
    """Delete the workspace AND its folder, with everything inside it."""
    workspace = _get_workspace(request, slug, for_edit=True)
    form = DestroyWorkspaceForm(request.POST, workspace=workspace)
    if not form.is_valid():
        messages.error(request, "; ".join(
            msg for errors in form.errors.values() for msg in errors))
        return redirect(_rev(request, "workspace", slug) + "?destroy=1")

    _teardown_quietly(workspace)
    try:
        result = services.destroy_workspace(workspace=workspace, user=request.user)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(_rev(request, "workspace", slug))

    messages.warning(
        request,
        f"{result['name']} destroyed — {result['files']} file(s) and "
        f"{result['folders']} folder(s) deleted permanently.")
    return redirect(_rev(request, "lobby"))


def _teardown_quietly(workspace):
    """Whatever runtime the kind has (a kernel, nothing) must not stop a
    workspace being removed — the registry's teardown hook swallows its own
    failures per app."""
    app = registry.for_kind(workspace.kind)
    if app is not None:
        app.teardown(workspace)
    # A closed workspace must stop billing: drop any raised time dials that
    # hang off it. No-op on hosts without the levy engine.
    from toto.quota import times

    times.clear_scope("ambrosia.Workspace", workspace.pk)


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

@login_required
def tree(request, slug):
    workspace = _get_workspace(request, slug)
    return _ok(items=filetree.flatten(workspace))


@login_required
def file_content(request, slug, pk):
    workspace = _get_workspace(request, slug)
    vault_file = _workspace_file(workspace, pk)
    try:
        content, truncated = services.read_file(vault_file)
    except ValidationError as exc:
        return _fail("; ".join(exc.messages), status=409)
    return _ok(pk=vault_file.pk, name=vault_file.title,
               file_type=vault_file.file_type, content=content,
               truncated=truncated,
               readonly=filetree.is_artifact(vault_file))


@login_required
@require_POST
def file_save(request, slug, pk):
    workspace = _get_workspace(request, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    if filetree.is_artifact(vault_file):
        # The tree greys these out, but the rule is enforced here: a hand-made
        # POST must not be able to overwrite a compile's output either, and the
        # next run would discard the edit regardless.
        return _fail("Generated files are read-only — edit the source instead.",
                     status=409)
    try:
        services.write_file(vault_file=vault_file,
                            content=_body(request).get("content", ""))
    except ValidationError as exc:
        return _fail("; ".join(exc.messages), status=409)
    return _ok(pk=vault_file.pk, size=vault_file.file_size_bytes)


@login_required
@require_POST
def file_create(request, slug):
    workspace = _get_workspace(request, slug, for_edit=True)
    payload = _body(request)
    directory = _resolve_dir(workspace, payload.get("directory"))
    try:
        vault_file = services.create_file(
            workspace=workspace, user=request.user,
            filename=payload.get("name", ""), directory=directory)
    except ValidationError as exc:
        return _fail("; ".join(exc.messages), status=409)
    return _ok(pk=vault_file.pk, name=vault_file.title,
               items=filetree.flatten(workspace))


@login_required
@require_POST
def dir_create(request, slug):
    workspace = _get_workspace(request, slug, for_edit=True)
    payload = _body(request)
    parent = _resolve_dir(workspace, payload.get("parent"))
    try:
        services.create_directory(workspace=workspace, user=request.user,
                                  name=payload.get("name", ""), parent=parent)
    except ValidationError as exc:
        return _fail("; ".join(exc.messages), status=409)
    return _ok(items=filetree.flatten(workspace))


@login_required
@require_POST
def file_rename(request, slug, pk):
    workspace = _get_workspace(request, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    try:
        services.rename_file(workspace=workspace, vault_file=vault_file,
                             name=_body(request).get("name", ""))
    except ValidationError as exc:
        return _fail("; ".join(exc.messages), status=409)
    return _ok(items=filetree.flatten(workspace))


@login_required
@require_POST
def file_delete(request, slug, pk):
    workspace = _get_workspace(request, slug, for_edit=True)
    vault_file = _workspace_file(workspace, pk)
    services.delete_file(workspace=workspace, vault_file=vault_file)
    return _ok(items=filetree.flatten(workspace))


def _resolve_dir(workspace, pk):
    """Same rule as _workspace_file: anywhere in the bucket, nowhere else.

    None still means the workspace's own root, so "New file" with nothing
    selected lands in your folder rather than at the bucket root.
    """
    if pk in (None, "", 0, "0"):
        return workspace.root_directory
    return get_object_or_404(
        VaultDirectory, pk=pk, bucket_id=workspace.bucket_id)


@login_required
def file_raw(request, slug, pk):
    """Stream a workspace file as itself — how the PDF preview gets its bytes.

    Served here rather than through vault's public URL so the output PDF of a
    private project does not have to be made public to be looked at.
    """
    workspace = _get_workspace(request, slug)
    vault_file = _workspace_file(workspace, pk)
    if vault_file.is_encrypted:
        raise Http404("No such file")
    content_type = {
        "pdf": "application/pdf",
        "image": "application/octet-stream",
    }.get(vault_file.file_type, "application/octet-stream")
    response = FileResponse(vault_file.file.open("rb"), content_type=content_type)
    # inline, so the browser's own PDF viewer renders it in the preview frame.
    response["Content-Disposition"] = f'inline; filename="{vault_file.title}"'
    return response


@login_required
@require_POST
def workspace_hibernate(request, slug):
    """Put this workspace to sleep and give its compute back.

    Owner only, and for a reason beyond permissions: hibernating RELEASES the
    reservation, so it hands capacity back to a pool other people draw on. That
    is the owner's to give.
    """
    workspace = _get_workspace(request, slug, for_edit=True)
    try:
        manifest = hibernation.hibernate(workspace, user=request.user)
    except hibernation.HibernationError as exc:
        return _fail(str(exc), status=409)
    return _ok(hibernated=True, manifest=manifest,
               depth=manifest.get("depth", "manifest"))


@login_required
@require_POST
def workspace_rehydrate(request, slug):
    """Wake it up — if there is capacity to wake it into.

    The refusal that matters is `pool_exhausted`: hibernating gave the
    reservation away, and waking asks for a new one. Reported as 409 rather
    than 500 because nothing is broken and nothing was lost; there is simply no
    room right now.
    """
    workspace = _get_workspace(request, slug, for_edit=True)
    try:
        manifest = hibernation.rehydrate(workspace, user=request.user)
    except hibernation.HibernationError as exc:
        return _fail(str(exc), status=409)
    return _ok(hibernated=False, manifest=manifest)
