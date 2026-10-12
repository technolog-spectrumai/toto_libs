#!/usr/bin/env bash
# Rebuild static/vault/deckview/deckview.{js,css} from the sources here. It
# needs a Morion checkout with its node_modules installed (the bundler and
# reveal.js 5.2.1): MORION names it. The sources are copied into that checkout
# for the build and removed again; nothing of Morion's is changed.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MORION="${MORION:-/home/janek/Desktop/dev/morion}"
WORK="$MORION/apps/morion/.toto_deckview_build"
trap 'rm -rf "$WORK"' EXIT
rm -rf "$WORK"; mkdir -p "$WORK"
cp "$HERE"/*.ts "$HERE/slide.css" "$HERE/vite.config.mjs" "$WORK/"
cd "$MORION/apps/morion"
OUT="$HERE/../static/vault/deckview" node "$MORION/node_modules/vite/bin/vite.js" build --config "$WORK/vite.config.mjs"
ls -la "$HERE/../static/vault/deckview/"
