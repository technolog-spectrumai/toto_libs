"""Commit DAG → positioned graph JSON for the Sigma.js history modal.

Sigma renders fixed coordinates (it does no layout), so the classic gitk lane
layout is computed here: commits arrive from ``git log --all --topo-order``
(newest first); each gets ``y = -row``. Lanes are columns: an ordered list of
"expected next sha" slots. A commit takes the leftmost lane expecting it (or
opens one), its first parent inherits that lane, extra parents open lanes to
the right, and duplicate expectations of the same parent collapse into the
leftmost lane once the parent is placed.
"""

from __future__ import annotations

from . import git_cli
from .models import GitRepo


def _lane_layout(commits: list[dict]) -> dict[str, int]:
    lanes: list[str | None] = []  # index = lane, value = sha expected next
    lane_of: dict[str, int] = {}

    for c in commits:
        sha, parents = c["sha"], c["parents"]

        # Take the leftmost lane expecting this sha, else open a new one.
        expecting = [i for i, s in enumerate(lanes) if s == sha]
        if expecting:
            lane = expecting[0]
            for i in expecting[1:]:  # merge duplicate expectations
                lanes[i] = None
        else:
            try:
                lane = lanes.index(None)
            except ValueError:
                lane = len(lanes)
                lanes.append(None)
        lane_of[sha] = lane

        # First parent continues in this lane; others get lanes to the right.
        if parents:
            lanes[lane] = parents[0]
            for p in parents[1:]:
                if p in lanes:
                    continue  # someone already expects it
                try:
                    free = lanes.index(None)
                    lanes[free] = p
                except ValueError:
                    lanes.append(p)
        else:
            lanes[lane] = None

        while lanes and lanes[-1] is None:
            lanes.pop()

    return lane_of


def history_graph(repo: GitRepo) -> dict:
    commits = git_cli.log_all(repo.worktree)
    ref_info = git_cli.refs(repo.worktree)
    sha_to_branches: dict[str, list[str]] = {}
    for name, sha in ref_info["branches"].items():
        sha_to_branches.setdefault(sha, []).append(name)

    lane_of = _lane_layout(commits)
    head_branch = ref_info["head"]
    head_sha = ref_info["branches"].get(head_branch) if head_branch else None

    nodes = []
    for row, c in enumerate(commits):
        sha = c["sha"]
        nodes.append({
            "id": sha,
            "x": lane_of[sha],
            "y": -row,
            "label": sha[:7],
            "message": c["message"],
            "author": c["author"],
            "date": c["date"],
            "refs": sorted(sha_to_branches.get(sha, [])),
            "is_head": sha == head_sha,
            "lane": lane_of[sha],
        })

    known = set(lane_of)
    edges = [
        {"source": c["sha"], "target": p}
        for c in commits
        for p in c["parents"]
        if p in known
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "head": head_branch,
        "branches": ref_info["branches"],
    }
