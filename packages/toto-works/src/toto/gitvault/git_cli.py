"""Thin wrapper over the ``git`` binary (installed unconditionally in the
portal image — see portal/deploy/Dockerfile).

Chosen over GitPython/pygit2/dulwich deliberately: zero extra deps, full
porcelain fidelity for merges and per-invocation HTTP auth, and it matches the
repo's binary-wrapper idiom (ffmpeg/tesseract). Only machine-readable output
formats are parsed (``--porcelain``/``-z``/``%x1f``-delimited pretty formats).
"""

from __future__ import annotations

import base64
import subprocess
from dataclasses import dataclass
from pathlib import Path

# Field / record separators for pretty formats — never appear in git metadata.
FS = "\x1f"
RS = "\x1e"


class GitError(Exception):
    def __init__(self, message: str, result: "GitResult | None" = None):
        super().__init__(message)
        self.result = result


class MergeConflict(Exception):
    def __init__(self, paths: list[str]):
        super().__init__(f"merge conflicts in {len(paths)} file(s)")
        self.paths = paths


@dataclass
class GitResult:
    code: int
    stdout: str
    stderr: str


def run_git(
    args: list[str],
    cwd: Path,
    extra_env: dict | None = None,
    timeout: int = 120,
    check: bool = True,
) -> GitResult:
    env = {
        "GIT_TERMINAL_PROMPT": "0",  # never hang on a credential prompt
        "HOME": str(cwd),            # ignore any global/user git config
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    result = GitResult(proc.returncode, proc.stdout, proc.stderr)
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.strip() or proc.stdout.strip() or f"git {args[0]} failed", result)
    return result


def identity_env(user) -> dict:
    """Author/committer identity for the acting portal user, passed via env so
    nothing is written to the repo's config."""
    name = (user.get_full_name() or user.username).strip() or user.username
    email = user.email or f"{user.username}@noreply.localhost"
    return {
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }


def auth_config_args(username: str, token: str) -> list[str]:
    """Per-invocation HTTP basic auth — credentials never persist in .git/config."""
    b64 = base64.b64encode(f"{username}:{token}".encode()).decode()
    return ["-c", f"http.extraHeader=Authorization: Basic {b64}"]


# --------------------------------------------------------------------------
# Porcelain helpers
# --------------------------------------------------------------------------

def init(worktree: Path, branch: str = "main") -> None:
    worktree.mkdir(parents=True, exist_ok=True)
    run_git(["init", "-b", branch], cwd=worktree)


def status(worktree: Path) -> list[dict]:
    """``git status --porcelain=v1 -z`` → [{"path", "state"}].

    v1 -z records are "XY path\\0" (with a second NUL-separated path after a
    rename record — folded into the same entry as ``path``/``from``).
    """
    out = run_git(["status", "--porcelain=v1", "-z"], cwd=worktree).stdout
    entries: list[dict] = []
    fields = out.split("\0")
    i = 0
    while i < len(fields):
        rec = fields[i]
        if not rec:
            i += 1
            continue
        state, path = rec[:2], rec[3:]
        entry = {"path": path, "state": state.strip()}
        if state[0] in ("R", "C"):  # rename/copy: next field is the source path
            i += 1
            entry["from"] = fields[i]
        entries.append(entry)
        i += 1
    return entries


def add_all(worktree: Path) -> None:
    run_git(["add", "-A"], cwd=worktree)


def commit(worktree: Path, message: str, user) -> str:
    run_git(["commit", "-m", message], cwd=worktree, extra_env=identity_env(user))
    return run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def head_branch(worktree: Path) -> str:
    return run_git(["symbolic-ref", "--short", "HEAD"], cwd=worktree).stdout.strip()


def branch_list(worktree: Path) -> list[str]:
    out = run_git(
        ["for-each-ref", "refs/heads", "--format=%(refname:short)"], cwd=worktree
    ).stdout
    return [b for b in out.splitlines() if b]


def branch_create(worktree: Path, name: str) -> None:
    run_git(["branch", "--", name], cwd=worktree)


def checkout(worktree: Path, branch: str) -> None:
    run_git(["checkout", branch, "--"], cwd=worktree)


def merge(worktree: Path, branch: str, user) -> str:
    """Merge ``branch`` into HEAD. Clean merges only: any conflict aborts the
    merge (worktree restored) and raises MergeConflict with the paths."""
    res = run_git(
        ["merge", "--no-edit", "--", branch],
        cwd=worktree,
        extra_env=identity_env(user),
        check=False,
    )
    if res.code != 0:
        conflicted = run_git(
            ["diff", "--name-only", "--diff-filter=U", "-z"], cwd=worktree
        ).stdout
        paths = [p for p in conflicted.split("\0") if p]
        run_git(["merge", "--abort"], cwd=worktree, check=False)
        if paths:
            raise MergeConflict(paths)
        raise GitError(res.stderr.strip() or "merge failed", res)
    return run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def log_all(worktree: Path) -> list[dict]:
    """All commits, topo order (newest first):
    [{"sha", "parents": [...], "author", "date", "message"}]."""
    fmt = FS.join(["%H", "%P", "%an", "%aI", "%s"]) + RS
    res = run_git(
        ["log", "--all", "--topo-order", f"--pretty=format:{fmt}"],
        cwd=worktree,
        check=False,
    )
    if res.code != 0:  # empty repo (no commits yet)
        return []
    commits = []
    for rec in res.stdout.split(RS):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, parents, author, date, message = rec.split(FS)
        commits.append({
            "sha": sha,
            "parents": parents.split() if parents else [],
            "author": author,
            "date": date,
            "message": message,
        })
    return commits


def refs(worktree: Path) -> dict:
    """{"branches": {name: sha}, "head": current branch or None (detached)}."""
    out = run_git(
        ["for-each-ref", "refs/heads", "--format=%(refname:short) %(objectname)"],
        cwd=worktree,
    ).stdout
    branches = {}
    for line in out.splitlines():
        if line.strip():
            name, sha = line.rsplit(" ", 1)
            branches[name] = sha
    head = run_git(["symbolic-ref", "--short", "HEAD"], cwd=worktree, check=False)
    return {"branches": branches, "head": head.stdout.strip() if head.code == 0 else None}


def show_commit(worktree: Path, sha: str) -> dict:
    fmt = FS.join(["%H", "%an", "%aI", "%B"])
    meta = run_git(
        ["show", "--no-patch", f"--pretty=format:{fmt}", sha], cwd=worktree
    ).stdout
    full_sha, author, date, message = meta.split(FS, 3)
    files_out = run_git(
        ["show", "--name-status", "--pretty=format:", "-z", sha], cwd=worktree
    ).stdout
    fields = [f for f in files_out.split("\0")]
    files = []
    i = 0
    while i < len(fields):
        st = fields[i].strip()
        if not st:
            i += 1
            continue
        if st[0] in ("R", "C"):  # status, source, dest
            files.append({"status": st, "path": fields[i + 2], "from": fields[i + 1]})
            i += 3
        else:
            files.append({"status": st, "path": fields[i + 1]})
            i += 2
    return {
        "sha": full_sha,
        "author": author,
        "date": date,
        "message": message.strip(),
        "files": files,
    }


def remote_set(worktree: Path, url: str) -> None:
    existing = run_git(["remote"], cwd=worktree).stdout.split()
    if "origin" in existing:
        run_git(["remote", "set-url", "origin", url], cwd=worktree)
    else:
        run_git(["remote", "add", "origin", url], cwd=worktree)


def push(worktree: Path, branch: str, username: str, token: str, timeout: int = 300) -> GitResult:
    return run_git(
        [*auth_config_args(username, token), "push", "-u", "origin", branch],
        cwd=worktree,
        timeout=timeout,
    )


def pull(worktree: Path, branch: str, username: str, token: str, user, timeout: int = 300) -> GitResult:
    """Fetch + merge (clean merges only, like local merge)."""
    run_git(
        [*auth_config_args(username, token), "fetch", "origin"],
        cwd=worktree,
        timeout=timeout,
    )
    remote_ref = f"origin/{branch}"
    res = run_git(
        ["merge", "--no-edit", "--", remote_ref],
        cwd=worktree,
        extra_env=identity_env(user),
        check=False,
    )
    if res.code != 0:
        conflicted = run_git(
            ["diff", "--name-only", "--diff-filter=U", "-z"], cwd=worktree
        ).stdout
        paths = [p for p in conflicted.split("\0") if p]
        run_git(["merge", "--abort"], cwd=worktree, check=False)
        if paths:
            raise MergeConflict(paths)
        raise GitError(res.stderr.strip() or "pull merge failed", res)
    return res
