#!/usr/bin/env bash
# NOT IN USE (noted 2026-10-06). Kept as a record; nothing in this repository
# calls it, and the faros host is not checked against this library today
# (README.md, "Other hosts").
#
# What the script does when run: regenerate the `faros_vendor` branch as the
# source branch's tree filtered to the packages the faros host pins (see
# faros' requirements.toto.txt), for
#   git subtree pull --prefix=vendor/toto_libs ../toto_libs faros_vendor --squash
# Each run chains a single filter commit onto the previous vendor commit (or
# starts a fresh root on first use), so the branch is a linear record of
# vendor states. faros_vendor is a derived artifact: never develop on it,
# never merge it back.
#
# The source branch is dev_django5, the one branch work happens on. The
# `faros` branch this script named until 2026-10-06 no longer exists, and
# neither does `faros_vendor`; their last states are legacy/faros and
# legacy/faros_vendor (2026-07-26, suite 1.8).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_BRANCH="${1:-dev_django5}"
VENDOR_BRANCH="faros_vendor"
# Everything the faros host does not pin stays out of its vendored tree:
# twelve of the sixteen packages. The four kept are toto-base, toto-auth,
# toto-flow and toto-ops — the set legacy/faros_vendor carries, which is the
# last record of faros' pins this repository has. It was not checked against
# faros' own requirements.toto.txt on 2026-10-06, when the five packages
# younger than the old list (toto-ambrosia, toto-anastasia, toto-business,
# toto-economy, toto-media-ops) were added here.
EXCLUDE=(
    toto-ai toto-ambrosia toto-anastasia toto-business toto-chat toto-economy
    toto-geo toto-graph toto-media toto-media-ops toto-repo toto-works
)

src_commit=$(git rev-parse --verify "$SOURCE_BRANCH^{commit}")

GIT_INDEX_FILE="$(mktemp)"
export GIT_INDEX_FILE
trap 'rm -f "$GIT_INDEX_FILE"' EXIT

git read-tree "$src_commit^{tree}"
for pkg in "${EXCLUDE[@]}"; do
    git rm -r -q --cached --ignore-unmatch "packages/$pkg"
done
tree=$(git write-tree)

parent_args=()
if prev=$(git rev-parse --verify -q "$VENDOR_BRANCH^{commit}"); then
    if [ "$(git rev-parse "$prev^{tree}")" = "$tree" ]; then
        echo "$VENDOR_BRANCH already matches $SOURCE_BRANCH (${src_commit:0:8}); nothing to do."
        exit 0
    fi
    parent_args=(-p "$prev")
fi

commit=$(git commit-tree "$tree" "${parent_args[@]}" \
    -m "faros vendor tree from $SOURCE_BRANCH @ ${src_commit:0:8}")
git branch -f "$VENDOR_BRANCH" "$commit"
echo "$VENDOR_BRANCH -> $(git rev-parse --short "$VENDOR_BRANCH")"
