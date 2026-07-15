#!/usr/bin/env bash
# Install the toto library (editable) from this repository into the active venv.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pip install -e "$REPO_ROOT"
