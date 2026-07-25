#!/usr/bin/env bash
# Clean-environment packaging gate for the toto suite.
#
# Proves, in fresh venvs:
#   1. every package's sdist builds, and its wheel builds FROM the sdist
#      (MANIFEST.in proof)
#   2. the wheels install together offline and report the lockstep version
#   3. the payload is complete, disjoint and unchanged (tests/test_packaging.py)
#   4. Django system checks pass against the INSTALLED wheels, with the
#      repository source unable to shadow them (tests/test_django_check.py)
#   5. each dependency tier installs and passes checks on its own — the proof
#      that no package secretly needs one above it
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# toto.locations uses GIS fields on a default (BUILD_GEO=1) build, so the
# interpreter must be able to load the system GDAL. A conda python usually
# cannot (its libstdc++ predates the system libgdal) — override with
# PYTHON=/usr/bin/python3 in that case. (The BUILD_GEO=0 proofs in
# tests/test_django_check.py block the contrib.gis import outright, so they
# validate the GIS-off path even where GDAL is present.)
PYTHON="${PYTHON:-python3}"
VENV="$REPO_ROOT/.venv_test"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"
DIST="$REPO_ROOT/dist"
VERSION="$(cat "$REPO_ROOT/VERSION")"
PACKAGES=()
for pkg in "$REPO_ROOT"/packages/*/; do PACKAGES+=("$(basename "$pkg")"); done

echo "==> toto suite $VERSION — ${#PACKAGES[@]} packages: ${PACKAGES[*]}"

echo "==> partition + version coherence"
"$PYTHON" "$REPO_ROOT/scripts/check_package_graph.py"
"$PYTHON" "$REPO_ROOT/scripts/release.py" --check

echo "==> fresh venv at $VENV"
rm -rf "$VENV"
"$PYTHON" -m venv "$VENV"
"$PIP" install --quiet --upgrade pip build

echo "==> build every sdist, then every wheel FROM its sdist"
rm -rf "$DIST"
"$PY" "$REPO_ROOT/scripts/build_wheels.py" --sdist --out "$DIST" >/dev/null
ls "$DIST"/*.whl | while read -r whl; do echo "    $(basename "$whl")"; done

echo "==> install test dependencies (Django and friends)"
"$PIP" install --quiet -r "$REPO_ROOT/tests/requirements-test.txt"

echo "==> install the suite offline (--no-index: only the wheels just built)"
PINS=()
for name in "${PACKAGES[@]}"; do PINS+=("$name==$VERSION"); done
"$PIP" install --quiet --no-index --find-links "$DIST" "${PINS[@]}"

echo "==> version + coherence of the installed suite"
# -I (isolated): keep the invoking cwd off sys.path so no source tree shadows
# the installed wheels.
"$PY" -I - "$VERSION" "${PACKAGES[@]}" <<'PY'
import importlib.metadata as md
import sys

from toto.versioning import check_runtime_coherence

expected, *packages = sys.argv[1:]
for name in packages:
    found = md.version(name)
    assert found == expected, f"{name} is {found}, expected {expected}"
    print(f"     {name} {found}")
check_runtime_coherence()
print("     suite coherent")
PY

echo "==> pytest"
cd "$REPO_ROOT"
TOTO_WHEEL_DIR="$DIST" "$VENV/bin/pytest" -q tests/

echo "==> tier matrix: each dependency tier must stand on its own"
# toto-base alone, then each package that only needs base, then the full stack.
# A hidden import from a lower tier into a higher one fails here and nowhere else.
TIERS=("toto-base" "toto-base toto-auth" "toto-base toto-flow" "toto-base toto-chat" "toto-base toto-ops" "toto-base toto-ai")
TIERS+=("toto-base toto-flow toto-works" "toto-base toto-flow toto-media" "toto-base toto-flow toto-geo" "toto-base toto-flow toto-ai toto-graph")
for tier in "${TIERS[@]}"; do
    TIER_VENV="$REPO_ROOT/.venv_tier"
    rm -rf "$TIER_VENV"
    "$PYTHON" -m venv "$TIER_VENV" >/dev/null
    "$TIER_VENV/bin/pip" install --quiet -r "$REPO_ROOT/tests/requirements-test.txt"
    TIER_PINS=()
    for name in $tier; do TIER_PINS+=("$name==$VERSION"); done
    "$TIER_VENV/bin/pip" install --quiet --no-index --find-links "$DIST" "${TIER_PINS[@]}"
    TOTO_TIER="$tier" "$TIER_VENV/bin/python" -I "$REPO_ROOT/tests/tier_check.py"
    echo "     OK: $tier"
    rm -rf "$TIER_VENV"
done

echo "==> clean-env check PASSED (suite $VERSION)"
