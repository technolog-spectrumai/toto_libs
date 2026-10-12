#!/usr/bin/env bash
# Rebuild static/vault/sheetview/sheetview.{js,css} from the sources here. It
# needs a Morion checkout with its node_modules installed (the bundler and
# Univer 0.25.1): MORION names it. The sources are copied into that checkout
# for the build and removed again; nothing of Morion's is changed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MORION="${MORION:-/home/janek/Desktop/dev/morion}"
WORK="$MORION/apps/morion/.toto_sheetview_build"
trap 'rm -rf "$WORK"' EXIT
rm -rf "$WORK"; mkdir -p "$WORK"
cp "$HERE"/*.ts "$HERE/vite.config.mjs" "$WORK/"
cd "$MORION/apps/morion"
OUT="$HERE/../static/vault/sheetview" node "$MORION/node_modules/vite/bin/vite.js" build --config "$WORK/vite.config.mjs"
ls -la "$HERE/../static/vault/sheetview/"
