"""High-level git operations: every mutating flow is
``repo_lock → export (if reading tree state) → git op → import (if the
checked-out content changed)``. Views stay thin JSON adapters over these."""

from __future__ import annotations

from django.conf import settings
from django.db import transaction

from toto.vault.models import VaultDirectory

from . import git_cli, sync
from .models import GitRepo


class GitvaultError(Exception):
    """User-facing error (400-class)."""


def create_repo(directory: VaultDirectory, user, default_branch: str = "") -> GitRepo:
    """The fast, synchronous part of init: nesting guard + the GitRepo row.
    The worktree materialization runs asynchronously via run_init (a GitRun)."""
    conflict = sync.nesting_conflict(directory)
    if conflict:
        raise GitvaultError(
            f"a git repo already exists on '{conflict.directory.full_path()}' "
            "(nested repos are not allowed)"
        )
    default_branch = (default_branch or "").strip() or "main"
    if any(c.isspace() for c in default_branch):
        raise GitvaultError("invalid branch name")
    with transaction.atomic():
        repo = GitRepo.objects.create(
            directory=directory, owner=user, default_branch=default_branch)
    # A brand-new repo must start from a clean slate: after a DB reset the
    # media volume can still hold a previous same-pk repo's worktree/.git,
    # whose history would otherwise leak into the new repo's initial commit.
    import shutil
    shutil.rmtree(repo.base_dir, ignore_errors=True)
    return repo


def run_init(repo: GitRepo, user) -> dict:
    """Materialize the worktree + initial commit. Idempotent — `git init` on
    an existing repo is safe and export/commit converge — so a failed init
    run can simply be re-dispatched on the same repo row."""
    with sync.repo_lock(repo):
        git_cli.init(repo.worktree, branch=repo.default_branch)
        result = sync.export_worktree(repo)
        git_cli.add_all(repo.worktree)
        sha = ""
        if git_cli.status(repo.worktree):
            sha = git_cli.commit(repo.worktree, "Initial commit", user)
    return {
        "stdout": (
            f"initialized; {len(result['written'])} file(s) exported"
            + (f"; initial commit {sha[:7]}" if sha else "")
        ),
        "stderr": "",
        "import_summary": None,
    }


def init_repo(directory: VaultDirectory, user) -> GitRepo:
    """Synchronous composite (create + materialize) — used by tests and any
    caller that wants a ready repo immediately."""
    repo = create_repo(directory, user)
    run_init(repo, user)
    return repo


def repo_status(repo: GitRepo) -> dict:
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        git_cli.add_all(repo.worktree)  # stage so status reflects renames/deletes
        dirty = git_cli.status(repo.worktree)
        branch = git_cli.head_branch(repo.worktree)
    encrypted = [
        vf.title for vf in sync.subtree_files(repo.directory) if vf.is_encrypted
    ]
    return {
        "branch": branch,
        "dirty": dirty,
        "encrypted_skipped": encrypted,
        "remote": {
            # "enabled" is Gitea's availability; a CUSTOM remote works without
            # it, so the panel shows the remote section when either holds.
            "enabled": bool(getattr(settings, "GITEA_ENABLED", False)),
            "connected": repo.remote_connected,
            "custom": bool(repo.remote_url),
            "clone_url": public_clone_url(repo) if repo.remote_connected else "",
        },
    }


def commit(repo: GitRepo, user, message: str) -> str:
    if not message.strip():
        raise GitvaultError("commit message is required")
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        git_cli.add_all(repo.worktree)
        if not git_cli.status(repo.worktree):
            raise GitvaultError("nothing to commit")
        return git_cli.commit(repo.worktree, message.strip(), user)


def branches(repo: GitRepo) -> dict:
    # Read-only (symbolic-ref + for-each-ref) — no export, no worktree/index
    # mutation, so it takes no repo_lock. Locking here only created a race with
    # the concurrent status call the repo panel fires alongside it (both grab
    # the same non-blocking lock → one spuriously 409s "busy").
    return {
        "current": git_cli.head_branch(repo.worktree),
        "branches": git_cli.branch_list(repo.worktree),
    }


def branch_create(repo: GitRepo, name: str, checkout: bool, user) -> dict:
    name = name.strip()
    if not name or any(c.isspace() for c in name):
        raise GitvaultError("invalid branch name")
    with sync.repo_lock(repo):
        git_cli.branch_create(repo.worktree, name)
        summary = None
        if checkout:
            git_cli.checkout(repo.worktree, name)
            summary = sync.import_worktree(repo)  # no-op content-wise, keeps flow uniform
    return {"ok": True, "import_summary": summary}


def branch_delete(repo: GitRepo, name: str) -> dict:
    name = name.strip()
    if not name:
        raise GitvaultError("branch name is required")
    with sync.repo_lock(repo):
        if name == git_cli.head_branch(repo.worktree):
            raise GitvaultError("cannot delete the current branch — switch first")
        git_cli.branch_delete(repo.worktree, name)
    return {"ok": True}


def checkout(repo: GitRepo, branch: str, user) -> dict:
    with sync.repo_lock(repo):
        # Fold any uncommitted vault-side edits in first so checkout doesn't
        # clobber them silently; git refuses if they'd be overwritten.
        sync.export_worktree(repo)
        git_cli.checkout(repo.worktree, branch)
        summary = sync.import_worktree(repo)
    return {"ok": True, "import_summary": summary}


def merge(repo: GitRepo, branch: str, user, resolutions=None) -> dict:
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        # raises MergeConflict (with per-file detail) when unresolved
        sha = git_cli.merge(repo.worktree, branch, user, resolutions=resolutions)
        summary = sync.import_worktree(repo)
    return {"ok": True, "sha": sha, "import_summary": summary}


def restore_to(repo: GitRepo, sha: str, user) -> dict:
    """Restore the tree to ``sha`` as a new commit and fold it into the vault.

    Uncommitted vault-side edits are refused rather than silently swallowed
    into the restore: export first, and a dirty tree means someone has work
    that a restore would destroy without a trace of it in any commit.
    """
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        git_cli.add_all(repo.worktree)
        if git_cli.status(repo.worktree):
            raise GitvaultError(
                "there are uncommitted changes — commit them first, so the "
                "restore stays undoable")
        new_sha = git_cli.restore_to(repo.worktree, sha, user)
        summary = sync.import_worktree(repo)
    return {"ok": True, "sha": new_sha, "import_summary": summary}


# ---------------------------------------------------------------------------
# Remote (Gitea) — Phase B
# ---------------------------------------------------------------------------

def internal_clone_url(repo: GitRepo) -> str:
    base = getattr(settings, "GITEA_INTERNAL_URL", "http://gitea:3000").rstrip("/")
    return f"{base}/{repo.remote_owner}/{repo.remote_name}.git"


def public_clone_url(repo: GitRepo) -> str:
    if repo.remote_url:
        return repo.remote_url
    gitea_path = getattr(settings, "GITEA_URL", "/gitea/").rstrip("/")
    return f"{gitea_path}/{repo.remote_owner}/{repo.remote_name}.git"


def connect_remote(repo: GitRepo, user, name: str = "", existing: str = "",
                   url: str = "") -> dict:
    """Wire the repo to a remote. Three ways in: a custom ``url`` (any git
    remote, no Gitea involved), an EXISTING Gitea repo (``existing`` =
    "owner/name"), or CREATE a new Gitea one (default). Sets origin; does not
    push/pull (the user does that). Whichever side connects clears the other —
    a repo has one origin, and half-stale remote fields are how a push lands
    somewhere nobody expects."""
    url = (url or "").strip()
    if url:
        if not (url.startswith(("http://", "https://", "git://", "ssh://"))
                or "@" in url):
            raise GitvaultError("that does not look like a git remote URL")
        repo.remote_url = url
        repo.remote_owner = ""
        repo.remote_name = ""
        repo.save(update_fields=["remote_url", "remote_owner", "remote_name"])
        with sync.repo_lock(repo):
            git_cli.remote_set(repo.worktree, url)
        return {"remote_url_public": url}

    if not getattr(settings, "GITEA_ENABLED", False):
        raise GitvaultError("Gitea is not enabled on this deployment")
    from . import gitea_client

    account = gitea_client.ensure_account(user)
    if existing:
        if "/" not in existing:
            raise GitvaultError("invalid repository selection")
        repo.remote_owner, repo.remote_name = existing.split("/", 1)
    else:
        repo.remote_owner = account.username
        repo.remote_name = gitea_client.create_repo(account, name or repo.directory.name)
    repo.remote_url = ""
    repo.save(update_fields=["remote_owner", "remote_name", "remote_url"])
    with sync.repo_lock(repo):
        git_cli.remote_set(repo.worktree, internal_clone_url(repo))
    return {"remote_url_public": public_clone_url(repo)}


def _remote_credentials(repo: GitRepo, user) -> tuple[str, str]:
    """(username, token) for the repo's remote — empty pair for a custom URL.

    Only the co-deployed Gitea gets minted credentials. A custom remote is
    pushed to verbatim; whatever auth its URL carries is all the auth there is.
    """
    if repo.remote_url:
        return "", ""
    from . import gitea_client

    account = gitea_client.ensure_account(user)
    return account.username, account.get_token()


def run_push(repo: GitRepo, user) -> dict:
    """Executed inside a GitRun (celery or inline)."""
    username, token = _remote_credentials(repo, user)
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        branch = git_cli.head_branch(repo.worktree)
        res = git_cli.push(repo.worktree, branch, username, token)
    return {"stdout": res.stdout, "stderr": res.stderr, "import_summary": None}


def run_pull(repo: GitRepo, user) -> dict:
    username, token = _remote_credentials(repo, user)
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        branch = git_cli.head_branch(repo.worktree)
        res = git_cli.pull(repo.worktree, branch, username, token, user)
        summary = sync.import_worktree(repo)
    return {"stdout": res.stdout, "stderr": res.stderr, "import_summary": summary}
