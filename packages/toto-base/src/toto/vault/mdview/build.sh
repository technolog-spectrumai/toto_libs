#!/usr/bin/env bash
# Rebuild static/vault/mdview/mdview.js from the sources here. It needs a
# Morion checkout with its node_modules installed (the bundler, markdown-it,
# markdown-it-footnote): MORION names it.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MORION="${MORION:-/home/janek/Desktop/dev/morion}"
cd "$MORION"
MORION="$MORION" node node_modules/vite/bin/vite.js build --config "$HERE/vite.config.mjs"
ls -la "$HERE/../static/vault/mdview/mdview.js"
