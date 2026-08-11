"""Glue for other apps (editors, vault browser). Import cheaply and only when
``django_apps.is_installed("toto.gitvault")``."""

from __future__ import annotations

from django.urls import reverse

from .models import GitRepo


def repo_for_directory(directory) -> GitRepo | None:
    """The repo whose subtree contains *directory* (itself or an ancestor)."""
    node = directory
    while node is not None:
        repo = GitRepo.objects.filter(directory=node).select_related("directory").first()
        if repo:
            return repo
        node = node.parent
    return None


def repo_urls(repo: GitRepo) -> dict:
    return {
        "status": reverse("gitvault:status", args=[repo.pk]),
        "commit": reverse("gitvault:commit", args=[repo.pk]),
        "branches": reverse("gitvault:branches", args=[repo.pk]),
        "branch_create": reverse("gitvault:branch_create", args=[repo.pk]),
        "branch_delete": reverse("gitvault:branch_delete", args=[repo.pk]),
        "checkout": reverse("gitvault:checkout", args=[repo.pk]),
        "merge": reverse("gitvault:merge", args=[repo.pk]),
        "history": reverse("gitvault:history", args=[repo.pk]),
        "commit_detail_base": reverse("gitvault:history", args=[repo.pk]).replace(
            "history/", "commits/"
        ),
        "restore_base": reverse("gitvault:history", args=[repo.pk]).replace(
            "history/", "restore/"
        ),
        "connect": reverse("gitvault:connect", args=[repo.pk]),
        "push": reverse("gitvault:push", args=[repo.pk]),
        "pull": reverse("gitvault:pull", args=[repo.pk]),
        # JS substitutes the real run id for the /0/ placeholder.
        "run_status_base": reverse("gitvault:run_status", args=[0]),
    }


def context_for_directory(directory, user) -> dict | None:
    """Toolbar context for a directory: the covering repo, or how to make one.

    None below the GITVAULT_ACCESS gate — that is what hides every surface's
    git toolbar from non-staff (the endpoints refuse independently).

    Two shapes. A repo covering *directory* (itself or an ancestor) yields the
    usual ``{"repo_pk", "repo_name", "urls"}``; no repo yields
    ``{"init_url", "dir_name"}`` so the surface can offer "Initialize
    repository" itself. Init used to be reachable only from the vault browser's
    per-directory dropdown — with git gone from the vault UI, each surface has
    to carry its own way in, and this is that way.

    None also for no directory at all (a file at a bucket root can never be in
    a repo — a repo IS a directory).
    """
    from . import permissions

    if not permissions.can_use(user):
        return None
    if directory is None:
        return None
    repo = repo_for_directory(directory)
    if repo is not None:
        return {
            "repo_pk": repo.pk,
            "repo_name": repo.directory.name,
            "urls": repo_urls(repo),
        }
    return {
        "init_url": reverse("gitvault:init", args=[directory.pk]),
        "dir_name": directory.name,
    }


def context_for_file(vault_file, user) -> dict | None:
    """Editor-toolbar context for a vault file — the covering repo, or the init
    offer for its directory. Editors pass this straight to the shared
    ``gitvault/_git_ui.html`` include. None below the GITVAULT_ACCESS gate."""
    if vault_file.directory_id is None:
        return None
    return context_for_directory(vault_file.directory, user)
