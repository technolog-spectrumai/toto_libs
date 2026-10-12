# The sheet viewer's sources

Copied from Morion (`morion/apps/morion/src/`, branch `v0_1_0`, 2026-10-12)
and adapted, as `../mdview/` was; the owner: "copy univer sheets viewer - but
read only - to view csv files and .uson files - same trick that with markdown".

| file | what it is |
|---|---|
| `sheet.ts` | Morion's `formats/sheet.ts`, unchanged: the workbook format (a Univer snapshot), `workbookFromRows`, `loads` |
| `uson.ts` | Morion's `web/uson.ts`: the `.uson` envelope's reader, with its two imports pointed here |
| `ext.ts` | the one function `uson.ts` needs from Morion's `files/openWith.ts` |
| `csvRead.ts` | new: a CSV reader (Morion writes CSV and never reads one), the rules of Morion's writer read backwards |
| `main.ts` | new: what Morion's `SheetEditor.vue` does to mount Univer, without Vue and read only |
| `../static/vault/sheetview/sheetview.{js,css}` | the bundle (`window.TotoSheet`) with Univer 0.25.1 inside, built by `build.sh` and committed (about 10 MB, 2.6 MB gzipped) |

Read only: the workbook is not editable, the page has no toolbar, formula
bar or context menu, and nothing is ever written back to the file.

To change it: edit here, run `./build.sh` (it needs a Morion checkout with
`node_modules`), commit the new bundle.
