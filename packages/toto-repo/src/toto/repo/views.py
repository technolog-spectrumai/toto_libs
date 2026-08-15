"""Thin JSON adapters over services.py. Error contract:
RepoError → 400, RepoBusy → 409 {busy}, MergeConflict → 409 {conflicts},
GitError → 400 with git's stderr. Bodies arrive as FormData (the oya fetch
idiom); responses are JsonResponse."""

from __future__ import annotations

import functools

from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from toto.vault.models import VaultDirectory, VaultFile

from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import (InsufficientFunds, charge, check_funds,
                               price_for)

from . import git_cli, history, integration, permissions, services, sync
from .dispatch import create_git_run, dispatch_git_run
from .models import GitRepo, GitRun, RepoQuotaPolicy, RepoUsageEvent


def _get_repo(request, repo_pk: int) -> GitRepo:
    repo = get_object_or_404(GitRepo.objects.select_related("directory"), pk=repo_pk)
    if not repo.directory.user_can_access(request.user):
        raise Http404
    return repo


def _json_errors(view):
    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        # The staff gate, before anything else: git is a staff tool
        # (REPO_ACCESS). Living in this funnel means every JSON door —
        # including future ones — refuses identically.
        if not permissions.can_use(request.user):
            return JsonResponse({"error": permissions.refusal()}, status=403)
        try:
            return view(request, *args, **kwargs)
        except services.RepoError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except sync.RepoBusy as exc:
            return JsonResponse({"error": str(exc), "busy": True}, status=409)
        except git_cli.MergeConflict as exc:
            return JsonResponse(
                {"error": _("merge conflicts — aborted"), "conflicts": exc.paths,
                 "details": exc.details, "aborted": True},
                status=409,
            )
        except git_cli.GitError as exc:
            return JsonResponse({"error": str(exc)}, status=400)
        except (QuotaExceeded, InsufficientFunds) as exc:
            # 429 and 402. Handled here so every door refuses the same way,
            # including the guard that lives down in dispatch.create_git_run.
            return JsonResponse({"error": str(exc)}, status=exc.status_code)
    return wrapped


@login_required
@require_POST
@_json_errors
def init_repo(request, dir_pk: int):
    directory = get_object_or_404(VaultDirectory, pk=dir_pk)
    if not directory.user_can_access(request.user):
        raise Http404
    # Refuse before create_repo, not after: create_git_run does the metering
    # for every caller, but by the time it runs here the repo row and its
    # directory already exist, so a 429 would leave a repo nobody asked for.
    # A duplicated *check* is a read and costs nothing; only the charge below
    # must happen once.
    tariff = price_for(request.user, "repo")
    check_quota(RepoQuotaPolicy, "repo.run", 1, request.user)
    check_funds(request.user, tariff, "repo.run", 1)

    # Guard + row synchronously (400s keep their contract); the worktree
    # materialization runs as a GitRun — the client polls run_status.
    repo = services.create_repo(
        directory, request.user,
        default_branch=request.POST.get("default_branch", ""))
    run = create_git_run(request.user, repo, "init")
    _dispatch_recorded(run)
    return JsonResponse({
        "repo_pk": repo.pk,
        "run_id": run.pk,
        "urls": integration.repo_urls(repo),
    })


@login_required
@require_GET
@_json_errors
def status(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    return JsonResponse(services.repo_status(repo))


def _meter_op(request, repo, op: str) -> None:
    """Count one in-request git subprocess, and refuse when it is one too many.

    Raises QuotaExceeded / InsufficientFunds; `_json_errors` turns both into the
    right status. No idempotency key — running `git log` twice is two calls, and
    that is the whole point of counting them.

    The read-only doors are metered too, deliberately. `history_view` and
    `commit_detail` take the repo lock and fork git like the rest, and being the
    cheapest to call makes them the most attractive to hammer — the same
    reasoning vault's second upload door already carries: the other way onto one
    resource must not be the cheap one.
    """
    tariff = price_for(request.user, "repo")
    check_quota(RepoQuotaPolicy, "repo.op", 1, request.user)
    check_funds(request.user, tariff, "repo.op", 1)

    src = {"source_type": "repo.GitRepo", "source_id": str(repo.pk)}
    record_usage(RepoUsageEvent, "repo.op", 1, request.user,
                 source_label=op, **src)
    charge(request.user, tariff, "repo.op", 1, **src)


@login_required
@require_POST
@_json_errors
def commit(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "commit")
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
    _meter_op(request, repo, "branch")
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
def branch_delete(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "branch_delete")
    return JsonResponse(services.branch_delete(repo, request.POST.get("name", "")))


@login_required
@require_POST
@_json_errors
def checkout(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "checkout")
    return JsonResponse(services.checkout(repo, request.POST.get("branch", ""), request.user))


@login_required
@require_POST
@_json_errors
def merge(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "merge")
    resolutions = None
    raw = request.POST.get("resolutions", "")
    if raw:
        import json

        try:
            resolutions = json.loads(raw)
        except ValueError:
            return JsonResponse({"error": "resolutions is not valid JSON"}, status=400)
        if not isinstance(resolutions, dict):
            return JsonResponse({"error": "resolutions must be an object"}, status=400)
    return JsonResponse(services.merge(
        repo, request.POST.get("branch", ""), request.user,
        resolutions=resolutions))


@login_required
@require_POST
@_json_errors
def restore(request, repo_pk: int, sha: str):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "restore")
    return JsonResponse(services.restore_to(repo, sha, request.user))


@login_required
@require_GET
@_json_errors
def history_view(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "history")
    with sync.repo_lock(repo):
        return JsonResponse(history.history_graph(repo))


@login_required
@require_GET
@_json_errors
def commit_detail(request, repo_pk: int, sha: str):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "show")
    with sync.repo_lock(repo):
        return JsonResponse(git_cli.show_commit(repo.worktree, sha))


@login_required
@require_POST
@_json_errors
def connect(request, repo_pk: int):
    repo = _get_repo(request, repo_pk)
    _meter_op(request, repo, "connect")
    return JsonResponse(services.connect_remote(
        repo, request.user, url=request.POST.get("url", "")))


def _dispatch_recorded(run) -> None:
    """Dispatch; failures always end up recorded on the GitRun so the caller
    can return {run_id} unconditionally and the standard poll surfaces them.
    (The inline path records its own failures; this catch covers dispatch
    itself — e.g. a broker error on .delay() — which would otherwise leave
    the run PENDING forever.)"""
    try:
        dispatch_git_run(run)
    except Exception as exc:
        from .runner import fail_run

        fail_run(run, str(exc))


def _dispatch_op(request, repo_pk: int, op: str):
    repo = _get_repo(request, repo_pk)
    if not repo.remote_connected:
        return JsonResponse({"error": _("repository has no remote — connect one first")}, status=400)
    run = create_git_run(request.user, repo, op)
    _dispatch_recorded(run)
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
@_json_errors
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


