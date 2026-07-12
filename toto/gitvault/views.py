"""Thin JSON adapters over services.py. Error contract:
GitvaultError → 400, RepoBusy → 409 {busy}, MergeConflict → 409 {conflicts},
GitError → 400 with git's stderr. Bodies arrive as FormData (the oya fetch
idiom); responses are JsonResponse."""

from __future__ import annotations

import functools

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from toto.vault.models import VaultDirectory, VaultFile

from . import git_cli, history, integration, services, sync
from .dispatch import create_git_run, dispatch_git_run
from .models import GitRepo, GitRun


def _get_repo(request, repo_pk: int) -> GitRepo:
    repo = get_object_or_404(GitRepo.objects.select_related("directory"), pk=repo_pk)
    if not repo.directory.user_can_access(request.user):
        raise Http404
    return repo


def _json_errors(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            return view(request, *args, **kwargs)
        except services.GitvaultError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except sync.RepoBusy as exc:
            return JsonResponse({"error": str(exc), "busy": True}, status=409)
        except git_cli.MergeConflict as exc:
            return JsonResponse(
                {"error": "merge conflicts — aborted", "conflicts": exc.paths, "aborted": True},
                status=409,
            )
        except git_cli.GitError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
    return wrapped


@login_required
@require_POST
@_json_errors
def init_repo(request, dir_pk: int):
    directory = get_object_or_404(VaultDirectory, pk=dir_pk)
    if not directory.user_can_access(request.user):
        raise Http404
    repo = services.init_repo(directory, request.user)
    return JsonResponse({"repo_pk": repo.pk, "urls": integration.repo_urls(repo)})


@login_required
@require_GET
@_json_errors
def status(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.repo_status(repo))


@login_required
@require_POST
@_json_errors
def commit(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    sha = services.commit(repo, request.user, request.POST.get("message", ""))
    return JsonResponse({"sha": sha})


@login_required
@require_GET
@_json_errors
def branches(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.branches(repo))


@login_required
@require_POST
@_json_errors
def branch_create(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    result = services.branch_create(
        repo,
        request.POST.get("name", ""),
        request.POST.get("checkout") == "1",
        request.user,
    )
    return JsonResponse(result)


@login_required
@require_POST
@_json_errors
def checkout(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.checkout(repo, request.POST.get("branch", ""), request.user))


@login_required
@require_POST
@_json_errors
def merge(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.merge(repo, request.POST.get("branch", ""), request.user))


@login_required
@require_GET
@_json_errors
def history_view(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    with sync.repo_lock(repo):
        return JsonResponse(history.history_graph(repo))


@login_required
@require_GET
@_json_errors
def commit_detail(request, repo_pk: int, sha: str):
    repo = _get_repo(request, repo_pk)
    with sync.repo_lock(repo):
        return JsonResponse(git_cli.show_commit(repo.worktree, sha))


@login_required
@require_POST
@_json_errors
def connect(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.connect_remote(repo, request.user, request.POST.get("name", "")))


def _dispatch_op(request, repo_pk: int, op: str):
    repo = _get_repo(request, repo_pk)
    if not repo.remote_connected:
        return JsonResponse({"error": "repository is not connected to Gitea"}, status=400)
    run = create_git_run(request.user, repo, op)
    dispatch_git_run(run)
    return JsonResponse({"run_id": run.pk})


@login_required
@require_POST
@_json_errors
def push(request, repo_pk: int):
    return _dispatch_op(request, repo_pk, "push")


@login_required
@require_POST
@_json_errors
def pull(request, repo_pk: int):
    return _dispatch_op(request, repo_pk, "pull")


@login_required
@require_GET
def run_status(request, run_id: int):
    run = get_object_or_404(GitRun.objects.select_related("repo__directory"), pk=run_id)
    if not run.repo.directory.user_can_access(request.user):
        raise Http404
    return JsonResponse({
        "status": run.status,
        "op": run.op,
        "stdout": run.stdout,
        "stderr": run.stderr,
        "import_summary": run.import_summary,
    })


@login_required
@require_GET
def file_context(request, file_pk: int):
    vault_file = get_object_or_404(VaultFile.objects.select_related("directory"), pk=file_pk)
    ctx = integration.context_for_file(vault_file)
    if ctx is None:
        return JsonResponse({"in_repo": False})
    return JsonResponse({"in_repo": True, **ctx})
