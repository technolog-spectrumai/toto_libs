#!/usr/bin/env bash
# NOT IN USE (noted 2026-10-06). Kept as a record; nothing in this repository
# or in zenobia's monorepo calls it.
#
# zenobia is re-vendored by rsync today: the whole repository is copied over
# <monorepo>/vendor/toto_libs so the two are byte-identical (README.md, "How
# zenobia re-vendors"). That copy keeps limbo/ and all sixteen packages, and
# has no `zenobia_vendor` branch behind it. A tree made by this script, which
# drops limbo/, is therefore not what the monorepo carries any more.
#
# What the script did, and still does when run: regenerate the
# `zenobia_vendor` branch as the source branch's tree with limbo/ filtered
# out, for
#   git subtree pull --prefix=vendor/toto_libs ../toto_libs zenobia_vendor --squash
# Each run chains a single filter commit onto the previous vendor commit (or
# starts a fresh root on first use), so the branch is a linear record of
# vendor states. A vendor branch is a derived artifact: never develop on it,
# never merge it back.
#
# The source branch is dev_django5, the one branch work happens on. The
# `zenobia` branch this script named until 2026-10-06 no longer exists; its
# last states are legacy/zenobia and legacy/zenobia_vendor.
#
# zenobia PINS five of the sixteen packages (toto-base, toto-auth, toto-flow,
# toto-ops, toto-economy; see its requirements.toto.txt). Its vendored tree
# nevertheless carries all sixteen, because this library's own tests assert
# whole-suite counts — so, unlike the faros/aurelian filters, this one
# excludes no package, and scripts/clean_env_check.sh and tests/ still apply
# to the result (the filtered faros/aurelian trees cannot run them).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_BRANCH="${1:-dev_django5}"
VENDOR_BRANCH="zenobia_vendor"
# Nothing here: zenobia's vendored tree carries every package, the eleven it
# does not pin included (see the header). Kept so this script stays the same
# shape as its faros/aurelian siblings.
EXCLUDE=()

src_commit=$(git rev-parse --verify "$SOURCE_BRANCH^{commit}")

GIT_INDEX_FILE="$(mktemp)"
export GIT_INDEX_FILE
trap 'rm -f "$GIT_INDEX_FILE"' EXIT

git read-tree "$src_commit^{tree}"
for pkg in ${EXCLUDE[@]+"${EXCLUDE[@]}"}; do
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
    -m "zenobia vendor tree from $SOURCE_BRANCH @ ${src_commit:0:8}")
git branch -f "$VENDOR_BRANCH" "$commit"
echo "$VENDOR_BRANCH -> $(git rev-parse --short "$VENDOR_BRANCH")"
