#!/usr/bin/env bash
# Regenerate the `zenobia_vendor` branch: the `zenobia` branch tree with limbo/
# filtered out. zenobia is the full-platform host and pins the WHOLE suite (see
# zenobia's requirements.toto.txt), so unlike the faros/aurelian filters this one
# excludes no packages — only the parked apps in limbo/, which no host installs.
# The zenobia repo vendors toto from THIS branch:
#   git subtree pull --prefix=vendor/toto_libs ../toto_libs zenobia_vendor --squash
#
# zenobia_vendor is a derived artifact — never develop on it, never merge it
# back. Rerun this script whenever the zenobia branch moves. Each run chains a
# single filter commit onto the previous vendor commit (or starts a fresh root
# on first use), so the branch stays a linear record of vendor states.
#
# Because packages/ is vendored whole, zenobia's vendored tree is a complete
# suite checkout: scripts/clean_env_check.sh and tests/ still apply to it (the
# filtered faros/aurelian trees cannot run them).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_BRANCH="${1:-zenobia}"
VENDOR_BRANCH="zenobia_vendor"
# zenobia installs every package, so nothing here. Kept so this script stays the
# same shape as its faros/aurelian siblings: drop a name in if zenobia ever
# stops pinning one.
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
