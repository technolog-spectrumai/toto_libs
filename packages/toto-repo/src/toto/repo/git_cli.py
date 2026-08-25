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
    def __init__(self, paths: list[str], details: "list[dict] | None" = None):
        super().__init__(f"merge conflicts in {len(paths)} file(s)")
        self.paths = paths
        # Per-path resolution material — [{path, ours, theirs, merged,
        # editable}] — collected BEFORE the abort restored the tree, because
        # after it the conflicted content is gone. Absent on the pull path,
        # which stays reporting-only.
        self.details = details or []


@dataclass
class GitResult:
    code: int
    stdout: str
    stderr: str


#: Config forced onto EVERY invocation, via ``-c`` rather than a config file so
#: nothing that reaches the worktree can edit it.
#:
#: ``protocol.ext.allow=never`` is the important one. Git's ``ext::`` transport
#: runs the rest of the URL **as a shell command**, so a stored remote URL is
#: otherwise a remote-code-execution primitive. ``remote_urls`` refuses to store
#: such a URL; this refuses to act on one that reached ``.git/config`` by some
#: other road — a repository imported with a config already in it, or a URL
#: written before this rule existed.
#:
#: ``core.hooksPath=/dev/null`` is the same argument for a different mechanism:
#: hooks are not versioned, so nothing should ever have put one here, but a
#: worktree is a directory on disk and "exclude arbitrary execution" should not
#: rest on that being true.
#:
#: ``core.symlinks=false`` makes git materialise a symlink as a plain file
#: holding its target path. Without it, a repository fetched from a remote can
#: contain ``notes -> /etc/passwd``; ``sync.import_worktree`` walks the tree with
#: ``os.walk`` and opens what it finds, which follows the link and files the
#: target's bytes as a vault file.
#: ``protocol.file.allow`` is deliberately NOT set here. It was, and it broke
#: the pull-conflict test, which stands up an origin as a local directory rather
#: than running a server — a sensible, cheap way to exercise the real ``pull``.
#: Unlike ``ext::``, the file transport is not code execution, and
#: ``remote_urls`` already refuses ``file://`` and bare paths at the storage
#: door, so blocking it here bought very little and cost a good test.
HARDENING = (
    "protocol.ext.allow=never",
    "core.hooksPath=/dev/null",
    "core.symlinks=false",
)


def refuse_option_like(value: str, what: str) -> str:
    """Refuse a value git would read as an option, and return it otherwise.

    For the arguments that cannot take a ``--`` terminator. ``git checkout``
    reads ``--`` as "pathspecs follow", so ``checkout -- <branch>`` asks for a
    *file* named like the branch; the terminator has to come after, which leaves
    the branch itself in option position. Same for the revisions handed to
    ``read-tree`` and ``show``, which arrive straight off a URL.
    """
    if value.startswith("-"):
        raise GitError(f"{what} may not begin with “-”")
    return value


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
    hardened: list[str] = []
    for setting in HARDENING:
        hardened += ["-c", setting]
    proc = subprocess.run(
        ["git", *hardened, *args],
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


def branch_delete(worktree: Path, name: str) -> None:
    """-d, never -D: git's own refusal for an unmerged branch is the guard —
    deleting unmerged work from a UI needs a stronger word than a click."""
    run_git(["branch", "-d", "--", name], cwd=worktree)


def checkout(worktree: Path, branch: str) -> None:
    # No `--` can protect this one: for checkout it means "pathspecs follow",
    # so `checkout -- <branch>` asks for a FILE by that name. The terminator
    # has to come after, which leaves the branch in option position.
    run_git(["checkout", refuse_option_like(branch, "a branch name"), "--"],
            cwd=worktree)


#: Above this, a conflicted file offers ours/theirs only — shipping megabytes
#: of marker text to a browser textarea helps nobody.
CONFLICT_EDIT_CAP = 200 * 1024


def _conflict_details(worktree: Path, paths: list[str]) -> list[dict]:
    """Resolution material per conflicted path, read while the merge is still
    live: ours/theirs from the index stages, the marker text from the tree.
    Binary or oversized files get ``editable: False`` and empty bodies — the
    choice buttons still work, the edit box does not apply.
    """
    details = []
    for path in paths:
        entry = {"path": path, "ours": "", "theirs": "", "merged": "",
                 "editable": False}
        try:
            ours = run_git(["show", f":2:{path}"], cwd=worktree, check=False)
            theirs = run_git(["show", f":3:{path}"], cwd=worktree, check=False)
            merged = (worktree / path).read_text(encoding="utf-8")
            if max(len(ours.stdout), len(theirs.stdout), len(merged)) <= CONFLICT_EDIT_CAP:
                entry.update(ours=ours.stdout, theirs=theirs.stdout,
                             merged=merged, editable=True)
        except (UnicodeDecodeError, OSError):
            pass  # binary/unreadable — ours/theirs choices remain valid
        details.append(entry)
    return details


def merge(worktree: Path, branch: str, user,
          resolutions: "dict[str, object] | None" = None) -> str:
    """Merge ``branch`` into HEAD.

    Without ``resolutions``: clean merges only — any conflict collects
    per-file detail, aborts (worktree restored) and raises MergeConflict.

    With ``resolutions`` ({path: "ours" | "theirs" | {"content": str}}): the
    SAME merge is re-run and the conflicted paths are settled per the caller's
    choices, staged, and committed. Re-running instead of persisting a
    half-merged state is the point — a merge is deterministic, so nothing has
    to sit conflicted between two requests, and a crash mid-resolution leaves
    a clean tree, not a stuck one.
    """
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
        if not paths:
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            raise GitError(res.stderr.strip() or "merge failed", res)

        if not resolutions:
            details = _conflict_details(worktree, paths)
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            raise MergeConflict(paths, details)

        unresolved = [p for p in paths if p not in resolutions]
        if unresolved:
            details = _conflict_details(worktree, unresolved)
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            raise MergeConflict(unresolved, details)

        for path, choice in resolutions.items():
            if path not in paths:
                continue  # ignore resolutions for paths that did not conflict
            if choice == "ours":
                run_git(["checkout", "--ours", "--", path], cwd=worktree)
            elif choice == "theirs":
                run_git(["checkout", "--theirs", "--", path], cwd=worktree)
            elif isinstance(choice, dict) and isinstance(choice.get("content"), str):
                (worktree / path).write_text(choice["content"], encoding="utf-8")
            else:
                run_git(["merge", "--abort"], cwd=worktree, check=False)
                raise GitError(f"invalid resolution for {path}")
            run_git(["add", "--", path], cwd=worktree)

        leftover = run_git(
            ["diff", "--name-only", "--diff-filter=U", "-z"], cwd=worktree
        ).stdout
        if [p for p in leftover.split("\0") if p]:
            run_git(["merge", "--abort"], cwd=worktree, check=False)
            raise GitError("resolution left conflicts behind — merge aborted")
        run_git(["commit", "--no-edit"], cwd=worktree, extra_env=identity_env(user))
    return run_git(["rev-parse", "HEAD"], cwd=worktree).stdout.strip()


def restore_to(worktree: Path, sha: str, user) -> str:
    """Make a NEW commit whose tree is exactly ``sha``'s. History is kept.

    read-tree --reset sets the index to the old tree (including deletions of
    files added since) and -u makes the worktree follow — which checkout of a
    pathspec would not do, since it cannot delete. Deliberately not
    ``reset --hard``: that discards the later commits themselves, breaks any
    remote that already has them, and turns "undo" into "lose".
    """
    run_git(["read-tree", "-u", "--reset",
             refuse_option_like(sha, "a commit")], cwd=worktree)
    status = run_git(["status", "--porcelain=v1"], cwd=worktree).stdout.strip()
    if not status:
        raise GitError("already at that state — nothing to restore")
    run_git(
        ["commit", "-m", f"Restore to {sha[:7]}"],
        cwd=worktree,
        extra_env=identity_env(user),
    )
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
    sha = refuse_option_like(sha, "a commit")
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


def push(worktree: Path, branch: str, username: str = "", token: str = "", timeout: int = 300) -> GitResult:
    # No credentials — no provider claimed this URL — means no auth args at
    # all: the URL is used verbatim and a private remote fails with git's own
    # message, which is the honest one. toto.repo holds no credentials of its
    # own to offer.
    auth = auth_config_args(username, token) if username and token else []
    return run_git(
        [*auth, "push", "-u", "origin", branch],
        cwd=worktree,
        timeout=timeout,
    )


def pull(worktree: Path, branch: str, username: str, token: str, user, timeout: int = 300) -> GitResult:
    """Fetch + merge (clean merges only, like local merge)."""
    auth = auth_config_args(username, token) if username and token else []
    run_git(
        [*auth, "fetch", "origin"],
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
        # Read the resolution material BEFORE aborting: ours/theirs live in the
        # index stages, which --abort discards. A pull conflict is an ordinary
        # merge conflict and gets the same resolver, so it needs the same
        # detail — without it the UI could only report the paths and stop.
        details = _conflict_details(worktree, paths) if paths else []
        run_git(["merge", "--abort"], cwd=worktree, check=False)
        if paths:
            raise MergeConflict(paths, details)
        raise GitError(res.stderr.strip() or "pull merge failed", res)
    return res
