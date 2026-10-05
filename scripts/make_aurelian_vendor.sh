#!/usr/bin/env bash
# NOT IN USE (noted 2026-10-06). Kept as a record; nothing in this repository
# calls it, and the aurelian host is not checked against this library today
# (README.md, "Other hosts").
#
# What the script does when run: regenerate the `aurelian_vendor` branch as
# the source branch's tree filtered to the packages the aurelian host pins
# (see aurelian's requirements.toto.txt) — limbo/ is filtered out too
# (aurelian carries its revived ops apps in its own repo) — for
#   git subtree pull --prefix=vendor/toto_libs ../toto_libs aurelian_vendor --squash
# Each run chains a single filter commit onto the previous vendor commit (or
# starts a fresh root on first use), so the branch is a linear record of
# vendor states. aurelian_vendor is a derived artifact: never develop on it,
# never merge it back.
#
# The source branch is dev_django5, the one branch work happens on. The
# `aurelian` branch this script named until 2026-10-06 no longer exists, and
# neither does `aurelian_vendor`; their last states are legacy/aurelian and
# legacy/aurelian_vendor (2026-07-26, suite 1.8).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_BRANCH="${1:-dev_django5}"
VENDOR_BRANCH="aurelian_vendor"
# Everything the aurelian host does not pin stays out of its vendored tree:
# eleven of the sixteen packages. aurelian pins toto-base, toto-auth,
# toto-flow and toto-works (its requirements.toto.txt, at suite 1.50 when read
# on 2026-10-06). toto-geo is kept as well: since 2026-10-04 toto-works
# depends on it (kanban keys into the map's models), so a 2.0 tree with
# toto-works and no toto-geo could not be installed.
EXCLUDE=(
    toto-ai toto-ambrosia toto-anastasia toto-business toto-chat toto-economy
    toto-graph toto-media toto-media-ops toto-ops toto-repo
)

src_commit=$(git rev-parse --verify "$SOURCE_BRANCH^{commit}")

GIT_INDEX_FILE="$(mktemp)"
export GIT_INDEX_FILE
trap 'rm -f "$GIT_INDEX_FILE"' EXIT

git read-tree "$src_commit^{tree}"
for pkg in "${EXCLUDE[@]}"; do
    git rm -r -q --cached --ignore-unmatch "packages/$pkg"
done
git rm -r -q --cached --ignore-unmatch limbo
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
    -m "aurelian vendor tree from $SOURCE_BRANCH @ ${src_commit:0:8}")
git branch -f "$VENDOR_BRANCH" "$commit"
echo "$VENDOR_BRANCH -> $(git rev-parse --short "$VENDOR_BRANCH")"
