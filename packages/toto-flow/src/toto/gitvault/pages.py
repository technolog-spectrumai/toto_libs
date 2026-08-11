"""The app's first page: the Git dashboard entry.

gitvault was JSON-only until now — every surface drove it through modals. What
was missing was the answer to "what repositories exist here at all", which the
dashboard tile used to dodge by linking straight to Gitea (staff-only, and
silent about local-only repos). This page is that answer: the codebase card
when Gitea exists, and the local repo list with a way IN to each repo's own
surface plus a read-only look at its history.

Read-only is enforced client-side only (the history modal hides its Restore
button on ``ctx.readOnly``) — the server endpoints behind it are the same
metered, access-checked ones every surface uses, so nothing new is exposed.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse

from toto.ui import PageProcessor

from . import git_cli, integration, permissions
from .models import GitRepo


def _surface_link(repo: GitRepo) -> dict:
    """Where this repo is actually USED — the surface that owns its directory.

    The first document or presentation in the subtree names its editor. A repo
    neither matches renders as a plain label: with git gone from the vault
    browser there is nowhere generic left to send anyone, and a dead link is
    worse than none.

    Workspace attribution used to come first here, through the reverse
    OneToOne from ambrosia.Workspace.root_directory. The workspace apps moved
    to the placidia host in 8/2026 and gitvault cannot follow (a federation
    child mounts no local OIDC provider, so deploy.py refuses both gitea and
    BUILD_GITVAULT on one) — so that branch is dead on both hosts and is gone.
    """
    from .sync import subtree_files

    for vault_file in subtree_files(repo.directory):
        if vault_file.file_type == "document" and django_apps.is_installed("toto.cyprian"):
            return {"label": vault_file.title, "kind": "document",
                    "url": reverse("cyprian:edit", args=[vault_file.pk])}
        if vault_file.file_type == "presentation" and django_apps.is_installed("toto.memo"):
            return {"label": vault_file.title, "kind": "presentation",
                    "url": reverse("memo:edit", args=[vault_file.pk])}
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
        "gitea_enabled": bool(getattr(settings, "GITEA_ENABLED", False)),
        "gitea_url": getattr(settings, "GITEA_URL", "/gitea/"),
    }
    return render(request, "gitvault/index.html",
                  PageProcessor().decorate(context, request))
