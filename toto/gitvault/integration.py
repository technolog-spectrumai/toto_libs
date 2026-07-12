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
        "checkout": reverse("gitvault:checkout", args=[repo.pk]),
        "merge": reverse("gitvault:merge", args=[repo.pk]),
        "history": reverse("gitvault:history", args=[repo.pk]),
        "commit_detail_base": reverse("gitvault:history", args=[repo.pk]).replace(
            "history/", "commits/"
        ),
        "connect": reverse("gitvault:connect", args=[repo.pk]),
        "push": reverse("gitvault:push", args=[repo.pk]),
        "pull": reverse("gitvault:pull", args=[repo.pk]),
        # JS substitutes the real run id for the /0/ placeholder.
        "run_status_base": reverse("gitvault:run_status", args=[0]),
    }


def context_for_file(vault_file) -> dict | None:
    """Editor-toolbar context for a vault file, or None when the file is not
    inside a git-enabled directory. Editors pass this straight to the shared
    ``gitvault/_git_ui.html`` include."""
    if vault_file.directory_id is None:
        return None
    repo = repo_for_directory(vault_file.directory)
    if repo is None:
        return None
    return {
        "repo_pk": repo.pk,
        "repo_name": repo.directory.name,
        "urls": repo_urls(repo),
    }
