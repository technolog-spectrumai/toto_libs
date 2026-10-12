# The deck player's sources

Copied from Morion (`morion/apps/morion/src/`, branch `v0_1_0`, 2026-10-12)
and adapted, as `../mdview/` and `../sheetview/` were; the owner: "now .pxml
player (presentations) - bring the code from morion".

| file | what it is |
|---|---|
| `presentation.ts`, `xml.ts` | Morion's `formats/`, unchanged: the `.pxml` format, its reader, layouts, themes |
| `scrubFragment.ts`, `scrub.ts`, `svgDataUri.ts` | Morion's, unchanged: the allow-list, the deck's scrubber, a drawing as an inert picture |
| `playDeck.ts` | Morion's `web/playDeck.ts` (a deck made fit to present from somebody else's file), imports pointed here, without its last two functions (`drawFormulas` is in `main.ts`, with the page's KaTeX handed in) |
| `slide.css` | Morion's slide stylesheet, unchanged |
| `main.ts` | new: what Morion's `DeckPlayer.vue` and `SlideBlock.vue` build as templates, built as DOM with the same classes, and reveal.js started with the same options |
| `../static/vault/deckview/deckview.{js,css}` | the bundle (`window.TotoDeck`) with reveal.js 5.2.1 inside, built by `build.sh` and committed |

A deck presents as in Morion: 1280 by 720, the same layouts, themes and
fonts, formulas drawn by KaTeX 0.16.47 (the page loads `vendor/katex`, which
a host's `download_vendor.py` fetches, only when the deck has one; Morion's
options, `trust` off). Nothing is written back: the player has no save.

To change it: edit here, run `./build.sh` (it needs a Morion checkout with
`node_modules`), commit the new bundle.
