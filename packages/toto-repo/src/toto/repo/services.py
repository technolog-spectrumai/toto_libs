"""High-level git operations: every mutating flow is
``repo_lock → export (if reading tree state) → git op → import (if the
checked-out content changed)``. Views stay thin JSON adapters over these."""

from __future__ import annotations

from django.db import transaction
from django.utils.translation import gettext as _

from toto.vault.models import VaultDirectory

from . import git_cli, remotes, sync
from .models import GitRepo


class RepoError(Exception):
    """User-facing error (400-class)."""


def create_repo(directory: VaultDirectory, user, default_branch: str = "") -> GitRepo:
    """The fast, synchronous part of init: nesting guard + the GitRepo row.
    The worktree materialization runs asynchronously via run_init (a GitRun)."""
    conflict = sync.nesting_conflict(directory)
    if conflict:
        raise RepoError(
            _("a git repo already exists on '%(path)s' "
              "(nested repos are not allowed)")
            % {"path": conflict.directory.full_path()}
        )
    default_branch = (default_branch or "").strip() or "main"
    if any(c.isspace() for c in default_branch):
        raise RepoError(_("invalid branch name"))
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
            "connected": repo.remote_connected,
            "url": repo.remote_url,
            # Whether anything installed here can authenticate that URL. Not a
            # gate — an unclaimed remote is pushed to verbatim — but the panel
            # says which of the two is happening, so a failed push against a
            # private remote reads as "nothing here holds credentials for this"
            # rather than as a bug.
            "authenticated_by": _provider_name(repo.remote_url),
        },
    }


def _provider_name(url: str) -> str:
    provider = remotes.registry.provider_for(url) if url else None
    return provider.name if provider else ""


def commit(repo: GitRepo, user, message: str) -> str:
    if not message.strip():
        raise RepoError(_("commit message is required"))
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        git_cli.add_all(repo.worktree)
        if not git_cli.status(repo.worktree):
            raise RepoError(_("nothing to commit"))
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
        raise RepoError(_("invalid branch name"))
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
        raise RepoError(_("branch name is required"))
    with sync.repo_lock(repo):
        if name == git_cli.head_branch(repo.worktree):
            raise RepoError(_("cannot delete the current branch — switch first"))
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
            raise RepoError(
                _("there are uncommitted changes — commit them first, so the "
                  "restore stays undoable"))
        new_sha = git_cli.restore_to(repo.worktree, sha, user)
        summary = sync.import_worktree(repo)
    return {"ok": True, "sha": new_sha, "import_summary": summary}


# ---------------------------------------------------------------------------
# Remote
# ---------------------------------------------------------------------------

def connect_remote(repo: GitRepo, user, url: str = "") -> dict:
    """Point the repo's origin at ``url``. One way in, because there is one
    origin: a repository holds a URL and nothing else, so there is no second
    field to leave half-stale and no rule about which spelling wins.

    Sets origin only; it does not push or pull. The user does that, and the
    difference matters — connecting a remote should never move anyone's commits
    on its own.
    """
    url = (url or "").strip()
    if not url:
        raise RepoError(_("a remote URL is required"))
    if not (url.startswith(("http://", "https://", "git://", "ssh://"))
            or "@" in url):
        raise RepoError(_("that does not look like a git remote URL"))
    repo.remote_url = url
    repo.save(update_fields=["remote_url"])
    with sync.repo_lock(repo):
        git_cli.remote_set(repo.worktree, url)
    return {"url": url, "authenticated_by": _provider_name(url)}


def run_push(repo: GitRepo, user) -> dict:
    """Executed inside a GitRun (celery or inline)."""
    username, token = remotes.credentials_for(repo.remote_url, user)
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        branch = git_cli.head_branch(repo.worktree)
        res = git_cli.push(repo.worktree, branch, username, token)
    return {"stdout": res.stdout, "stderr": res.stderr, "import_summary": None}


def run_pull(repo: GitRepo, user) -> dict:
    username, token = remotes.credentials_for(repo.remote_url, user)
    with sync.repo_lock(repo):
        sync.export_worktree(repo)
        branch = git_cli.head_branch(repo.worktree)
        res = git_cli.pull(repo.worktree, branch, username, token, user)
        summary = sync.import_worktree(repo)
    return {"stdout": res.stdout, "stderr": res.stderr, "import_summary": summary}
