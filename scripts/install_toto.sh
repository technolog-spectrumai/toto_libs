#!/usr/bin/env bash
# Install the whole toto suite (editable) from this repository into the active venv.
#
# All packages go in ONE pip invocation: they pin each other exactly, so pip can
# only satisfy those pins when it sees every package at once.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ARGS=()
for pkg in "$REPO_ROOT"/packages/*/; do
    ARGS+=(-e "$pkg")
done

echo "==> pip install (editable): ${#ARGS[@]} packages at version $(cat "$REPO_ROOT/VERSION")"
pip install "${ARGS[@]}"

echo "==> installed:"
python - <<'PY'
import importlib.metadata as md
for dist in sorted(d.metadata["Name"] for d in md.distributions()
                   if (d.metadata["Name"] or "").startswith("toto")):
    print(f"    {dist} {md.version(dist)}")
PY
