#!/usr/bin/env bash
# Regenerate the `aurelian_vendor` branch: the `aurelian` branch tree filtered
# to the packages the aurelian host actually pins (see aurelian's
# requirements.toto.txt) — limbo/ is filtered out too (aurelian carries its
# revived ops apps in its own repo). The aurelian repo vendors toto from THIS
# branch:
#   git subtree pull --prefix=vendor/toto_libs ../toto_libs aurelian_vendor --squash
#
# aurelian_vendor is a derived artifact — never develop on it, never merge it
# back. Rerun this script whenever the aurelian branch moves. Each run chains a
# single filter commit onto the previous vendor commit (or starts a fresh root
# on first use), so the branch stays a linear record of vendor states.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SOURCE_BRANCH="${1:-aurelian}"
VENDOR_BRANCH="aurelian_vendor"
# Everything the aurelian host does not install stays out of its vendored tree.
EXCLUDE=(toto-ai toto-chat toto-geo toto-graph toto-media toto-ops toto-repo)

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
