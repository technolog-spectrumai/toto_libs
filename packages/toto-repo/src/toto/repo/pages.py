"""The app's first page: the local repository list.

toto.repo was JSON-only until it grew this — every surface drove it through
modals, and nothing answered "what repositories exist here at all". This page is
that answer: every local repo with a way IN to the surface that owns it, plus a
read-only look at its history.

It says nothing about Gitea, deliberately. The hosted-code card belongs to
``toto.gitea``, which is a separate app precisely because the host that has one
does not have the other.

Read-only is enforced client-side only (the history modal hides its Restore
button on ``ctx.readOnly``) — the server endpoints behind it are the same
metered, access-checked ones every surface uses, so nothing new is exposed.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from toto.ui import PageProcessor

from . import git_cli, integration, permissions
from .models import GitRepo


def _workspace_namespace(workspace) -> str:
    """The URL namespace of the lab that owns this workspace's kind.

    Asked of ambrosia's registry rather than hardcoded, for the same reason
    ambrosia itself asks: a build may install the Python lab, the LaTeX lab or
    both, and toto.repo must name none of them. Returns "" when the kind's app
    is not installed here — then there is no room to link to.
    """
    try:
        from toto.ambrosia import registry
    except ImportError:
        return ""
    app = registry.for_kind(workspace.kind)
    return app.namespace if app else ""


def _surface_link(repo: GitRepo) -> dict:
    """Where this repo is actually USED — the surface that owns its directory.

    A WORKSPACE first: its root_directory is a OneToOne to the very directory a
    GitRepo keys on, so the match is exact and needs no scan. Otherwise the
    first document or presentation in the subtree names its editor. A repo
    matching neither renders as a plain label: with git gone from the vault
    browser there is nowhere generic left to send anyone, and a dead link is
    worse than none.

    Workspace attribution was deleted in 8/2026 on the premise that this app
    could never follow the labs to placidia. It ships in a wheel now and runs
    there — and after the split placidia is the ONLY host that runs it — so
    without this the host that owns the workspaces would be the one host whose
    repositories all render as unclickable folder paths.
    """
    from .sync import subtree_files

    if django_apps.is_installed("toto.ambrosia"):
        workspace = getattr(repo.directory, "ambrosia_workspace", None)
        if workspace is not None:
            app = _workspace_namespace(workspace)
            if app:
                return {"label": workspace.name, "kind": "workspace",
                        "url": reverse(f"{app}:workspace", args=[workspace.slug])}

    for vault_file in subtree_files(repo.directory):
        if vault_file.file_type == "document" and django_apps.is_installed("toto.cyprian"):
            return {"label": vault_file.title, "kind": "document",
                    "url": reverse("cyprian:edit", args=[vault_file.pk])}
    return {"label": repo.directory.full_path(), "kind": "folder", "url": ""}


@login_required
def index(request):
    if not permissions.can_use(request.user):
        from django.core.exceptions import PermissionDenied

        raise PermissionDenied(permissions.refusal())
    repos = []
    # Filtered in Python through the SAME gate every endpoint uses
    # (user_can_access), not a parallel queryset reimplementation of it — two
    # authorization spellings is how they drift. Repo counts are small.
    candidates = (
        GitRepo.objects.select_related("directory", "directory__bucket")
        .order_by("-created_at")
    )
    for repo in candidates:
        if not repo.directory.user_can_access(request.user):
            continue
        try:
            branch = git_cli.head_branch(repo.worktree)
        except git_cli.GitError:
            branch = ""  # worktree not materialized yet (init still running)
        repos.append({
            "repo": repo,
            "branch": branch,
            "surface": _surface_link(repo),
            "urls": integration.repo_urls(repo),
        })

    context = {
        "repos": repos,
        # One ctx per repo for the History buttons — keyed by pk, parsed once.
        "repo_map": {
            str(row["repo"].pk): {
                "repoPk": row["repo"].pk,
                "repoName": row["repo"].directory.name,
                "readOnly": True,
                "urls": row["urls"],
            }
            for row in repos
        },
    }
    return render(request, "repo/index.html",
                  PageProcessor().decorate(context, request))
