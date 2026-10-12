# The Markdown viewer's sources

Copied from Morion (`morion/apps/morion/src/markdown/`, branch `v0_1_0`,
2026-10-12) and adapted; the owner: "copy the code and adapt it rather than
porting morion - it is ok to duplicate code. morion code is well tested."

| file | what it is |
|---|---|
| `render.ts`, `anchors.ts`, `scrubFragment.ts`, `shims.d.ts` | Morion's, unchanged: markdown-it with the GitHub rules, the heading addresses, the allow-list every rendered note goes through |
| `main.ts` | new: what Morion's `MarkdownPreview.vue` did around the renderer, without Vue (render, draw the mermaid diagrams) |
| `../static/vault/mdview/markdown.css` | Morion's stylesheet, unchanged |
| `../static/vault/mdview/mdview.js` | the bundle of the above (`window.TotoMarkdown`), built by `build.sh` and committed |

A note written for Morion reads the same here: tables, fenced code,
footnotes, heading permalinks and `[TOC]`, strikethrough, task lists, bare
links, mermaid diagrams, formulas in GitHub's spellings drawn by KaTeX. One
difference: a relative link or image is its text (no file is resolved from a
note).

Mermaid and KaTeX are not in the bundle: the page loads
`vendor/mermaid/mermaid.min.js` and `vendor/katex/katex.min.{js,css}` (a
host's `download_vendor.py` fetches them) only when a note has a diagram or
a formula.

To change the renderer: edit here, run `./build.sh` (it needs a Morion
checkout with `node_modules`), commit the new `mdview.js`.
