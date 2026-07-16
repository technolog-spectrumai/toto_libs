#!/usr/bin/env bash
# Clean-environment packaging gate for the toto library.
#
# Proves, in a fresh venv:
#   1. the sdist builds, and a wheel builds FROM the sdist (MANIFEST.in proof)
#   2. the wheel installs and `import toto` reports the expected version
#   3. the wheel payload is complete (tests/test_packaging.py)
#   4. Django system checks pass against the installed wheel, with the
#      repository source tree unable to shadow it (tests/test_django_check.py)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO_ROOT/.venv_test"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"

echo "==> fresh venv at $VENV"
rm -rf "$VENV"
python3 -m venv "$VENV"
"$PIP" install --quiet --upgrade pip build

echo "==> build sdist, then wheel FROM the sdist"
rm -rf "$REPO_ROOT/dist"
"$PY" -m build --sdist --outdir "$REPO_ROOT/dist" "$REPO_ROOT" >/dev/null
SDIST="$(ls "$REPO_ROOT"/dist/toto-*.tar.gz)"
"$PIP" wheel --quiet --no-deps --wheel-dir "$REPO_ROOT/dist" "$SDIST"
WHEEL="$(ls "$REPO_ROOT"/dist/toto-*.whl)"
echo "    built: $(basename "$SDIST"), $(basename "$WHEEL")"

echo "==> install wheel + import check"
"$PIP" install --quiet "$WHEEL"
# -I (isolated): keep the invoking cwd off sys.path so no source tree shadows
# the installed wheel.
"$PY" -I -c 'import toto; assert toto.__version__ == "0.3.2", toto.__version__; print("    import toto OK, version", toto.__version__)'

echo "==> test dependencies"
"$PIP" install --quiet -r "$REPO_ROOT/tests/requirements-test.txt"

echo "==> pytest"
cd "$REPO_ROOT"
TOTO_WHEEL="$WHEEL" "$VENV/bin/pytest" -q tests/

echo "==> clean-env check PASSED"
