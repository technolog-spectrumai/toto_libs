# toto.cyprian

A writer. Long documents with real pages, and a PDF that looks finished.

## Purpose

zenobia could make slide decks and run code, but there was nowhere to write a
*document*. The nearest things were captive: `kanban`'s documentation pages are
bound one-to-one to a Mission, and `socialhub` news is a single section inside a
community feed.

A document is **one self-contained XML file in the vault**,
`file_type="document"` — the same arrangement `toto.memo` uses for a deck. There
is nothing in the database. That is what makes a document movable, downloadable,
backup-able and shareable with the vault tools that already exist, and what means
it can never half-exist.

The structure is `toto.verbena`'s — a page of ordered sections — *serialised*
rather than stored in tables.

Optional: `BUILD_CYPRIAN`, offered by the builder as **Documents**.

## Screens

| Screen | What it is |
|---|---|
| **Library** (`/cyprian/`) | Documents you can open, each card a real rendered first page. Paginated. |
| **Writer** (`/cyprian/edit/<pk>/`) | Paginated canvas, drag-and-drop, and a **Source** tab holding the file itself. |
| **Reader** (`/cyprian/read/<pk>/`) | The document, sized for reading. |

The vault's **Play** and **Edit** buttons on a document go to the reader and the
writer.

## The pagination contract

Real pages in a browser editor are usually approximate, because the browser and
the print engine break content differently — and a page-per-page drift compounds
into being a whole page out by the end of a long document. This avoids that by
constraining both ends to agree on one rule:

> **The editor breaks pages only BETWEEN top-level nodes, and the PDF is told
> `break-inside: avoid` on every one of them.**

That leaves exactly one question — *does this fit in what is left of the page?* —
and both surfaces answer it identically. The `cyPagination` plugin measures each
top-level node against the A4 content box, accumulates, and marks the node that
opens a page; WeasyPrint, forbidden from splitting one, breaks in the same
places. **The page rules you see while writing are the pages you get.**

The gap is drawn as wide as the sheet and the colour of the desk behind it, so
text that passes the boundary visibly lands on a new page as you type. The
canvas is still ONE element: splitting it into real page elements would mean
moving nodes under a live ProseMirror view, which loses the caret.

The page height comes from the stylesheet's own custom properties
(`--cy-page-h`, `--cy-margin-y`), read off the element — not from the margin
name, because duplicating those numbers here is how the editor's pages and the
PDF's stop agreeing.

**The one case it cannot cover** is a paragraph, table or picture taller than a
whole page. There the rule is dropped so both sides split it the same way, and
the editor says *"taller than a page — this one will split"* on it. A stated
limitation beats a silent one.

## Renditions

**Download PDF** does what it says. **Save PDF** and **Save HTML** file the
result in the vault beside the document — the vault is the filesystem here, so a
PDF of a report belongs next to the report, where it can be shared and linked
like anything else. Saving twice overwrites: exporting is not history.

The link comes back in a modal. "Saved to your bucket" without one means going
to hunt for it, and the file is served back through cyprian's own
`rendition` view rather than the media URL, because these files are private and
MEDIA is served straight off disk by nginx with no idea who is asking.

The HTML is **standalone** in the same sense the XML is: the stylesheet is
inlined and the pictures are already data URIs, so it opens on a machine that
has never heard of this platform.

## One stylesheet, four surfaces

`static/cyprian/document.css` is the single definition of how a document looks:

```
document.css ──► the writer canvas   .cy-page > .cy-block
             ──► the reader          the same markup, no page boxes
             ──► the PDF export      @page + the same blocks
             ──► the library card    a scaled-down first page
```

Page geometry is CSS variables, so `page="a4|letter"` and
`margins="normal|narrow|wide"` are two attributes rather than four stylesheets.
Fonts are the same five system stacks memo uses, for the same reason: no webfont
is vendored, the file may not use `url()`, and a family the container lacks would
substitute silently and the PDF would stop matching the screen.

## Format — `document.xml`, v3

```xml
<document version="3" title="Quarterly Report"
          font="serif" margins="normal" toc="true">
  <meta><field name="author"><![CDATA[A. Lovelace]]></field></meta>
  <content><![CDATA[<h1>Introduction</h1>
    <p>Revenue is <strong>up</strong>.</p>
    <table><tr><th>Region</th><td>+12%</td></tr></table>]]></content>
</document>
```

**One body, and headings are the structure.** v1 was a list of typed blocks. v2
folded those into one rich-text document per section. v3 folds the sections away
too: a heading in the body does everything a section did — it sets the outline
and anchors the contents page — and it is one keystroke rather than a button, a
level and an ordering to manage.

`loads` reads **all three** and converts on the way in; `dumps` only ever writes
v3. Each conversion is one way, because the alternative is maintaining three
editors forever. A v2 section becomes an `<h1>`/`<h2>`/`<h3>` by its level
followed by its content, which is exactly what it rendered as anyway.

**The contents page is derived.** `document.outline` finds the headings and
`document.anchored_content` gives each one an id at render time, because the PDF
resolves those page numbers after layout with `target-counter()` and needs a
real anchor. Nothing is stored, so nothing can fall out of step.

**A4 only, and no section numbering.** A document that can be one of two paper
sizes needs a control for it, a rule in every stylesheet and a branch in the
paginator — for a choice almost nobody makes and nobody makes twice. `page` is
still parsed from older files and ignored.

The rules that survived from `toto.memo.presentation_format`:

- **The payload is CDATA-wrapped**, including a literal `]]>` (split across two
  sections). The only rule that cannot lose a byte.
- **Nothing unknown is discarded** — unknown attributes and child elements
  round-trip, and a v1 `<block>` whose *type* this build does not recognise has
  no HTML to become, so it is kept verbatim in `extra`.

## The PDF

The first page furniture in this repo. Between them `notarius/render.py` and
`memo/render_pdf.py` hold the only other two `@page` rules in the tree, and
neither declares a margin box, a page counter or a contents page.

- a **running header** carrying the document's own title, via `string-set` and
  `@top-center`, suppressed on the cover by `@page :first`;
- **page numbers** from `counter(page)` in `@bottom-center`;
- a **contents page whose numbers are real**, resolved after layout by
  `target-counter()`. That is the thing a browser cannot do and a print engine
  can — the number is the page the target actually landed on.

Section numbers are a CSS counter rather than digits baked into the text, so
inserting a section renumbers the rest for free and the stored heading stays what
the author typed.

Gated by `BUILD_WEASYPRINT`, lazily imported, with the named refusal
`notarius/render.py` established. The builder declares
`flags=("BUILD_CYPRIAN", "BUILD_WEASYPRINT")` so ticking Documents cannot
produce a deployment whose export only ever answers 503.

## Reuse, not reimplementation

| Reused | From | Why it transfers |
|---|---|---|
| `sanitize.py` | `toto.memo` | Pure stdlib. Inline/rich/SVG/KaTeX allowlists, already attacked by memo's tests. |
| `history.js` | `memo/static/memo/` | Snapshot undo with the deferred copy. |
| `sanitize.js` | `memo/static/memo/` | The client mirror of the Python allowlist. |
| `tiptap` | `cyprian/static/cyprian/vendor/` | The prose field. MIT, 54 ESM files, pinned and committed — see below. |
| `ace` | `core/static/vendor/` | The Source tab. Already vendored for the file editors. |
| `_formula_help.html` | `toto.memo` | The LaTeX cheatsheet, unchanged. |

Cyprian ships **in the same wheel as memo** (`toto-works`), which is what makes
those imports a plain intra-package import rather than a dependency between
distributions. It started life as a zenobia host app and moved into the wheel in
1.41, when delta wanted the same writer; `check_package_graph.py` now sees it and
enforces the partition. `checks.py` still turns a missing dependency into a
`manage.py check` error (`cyprian.E001`/`E002`) rather than a 500 on the first
save, because a host can install cyprian and forget `toto.memo`.

`toto.notarius` is reached only through `apps.is_installed` guards and function-
local imports: zenobia owns contracts, delta has none, and the wheel must not
depend on either.

## Vendoring TipTap without a bundler

Every other third-party library in this suite is a pre-built UMD bundle loaded
with a plain `<script src>`; there is no bundler, no `package.json` and no npm
step anywhere in the repo. TipTap v3 is ESM-only, and v2's UMD build expects
ProseMirror as bare globals (`window.state`, `window.model`) — not a supported
distribution path.

So cyprian is the one exception, and it is an **import map**:

- 54 pinned ESM files in `static/cyprian/vendor/tiptap/` (~1 MB), committed,
  with `LICENSE` and a `VERSION.txt` naming every package and source URL — the
  canasta/PixiJS precedent, and it keeps `vendor/toto_libs` clean, because
  dirtying the vendored subtree blocks every pinned build.
- `tiptap.py` builds the map from `manifest.json`, putting every module through
  `static()` so it is cache-busted with the rest of the site.
- `test_tiptap.py` walks every vendored file, extracts every bare specifier and
  asserts the map covers it. A gap there would be a 404 in a browser after a
  deploy, and nothing else in this repo would notice.

Two traps, both handled by vendoring under our own names: **no `.mjs`** (nginx
serves `/static/` with stock mime types, which has no entry for it, so the
browser would refuse the module — and only in production), and two packages
whose ESM is not at `dist/index.js`.

Refresh the pin with `scripts/fetch_tiptap.py`, run by hand, never at build time.

## Writing — TipTap, with our own toolbar

TipTap ships **no UI at all**, which is the point of choosing it: the toolbar is
ours, the extensions are configured here, and nothing has to be fought into
shape the way Trix's did. Headings, bold, italic, underline, strike, inline
code, bulleted and numbered lists, quotes, code blocks, links, images, tables,
formulas, callouts, dividers and page breaks — all from one row of buttons, and
**the table controls appear only when the caret is inside a table**, because
eight buttons that do nothing most of the time are the clutter this editor
exists to avoid.

**One editor, for the whole document.** There is no section to select, add or
delete; you type, and a heading is how you say "new chapter".

Two things about that instance are load-bearing:

- **It lives in the factory closure, never on the Alpine component.** Alpine
  wraps component state in a reactive Proxy; ProseMirror compares
  `transaction.before` with `state.doc` by *identity*, and a proxied doc is
  never identical to the raw one it came from — every command threw *"Applying a
  mismatched transaction"* until the editor moved out of the proxy. `ui.marks`,
  a plain counter bumped on every selection change, is what the toolbar watches
  instead, so `isActive()` still re-evaluates.
- **The mount is retried on `requestAnimationFrame`.** `boot()` runs before
  Alpine has rendered the template, so the div the editor mounts into does not
  exist yet.
- **Pagination is a ProseMirror DECORATION.** The view owns its DOM and
  re-renders nodes from its state, so `data-page-start` attributes written from
  outside were wiped by the next keystroke — which is what the first attempt
  did. A decoration is the supported way to say "this node looks different", and
  it survives typing.

Memo's rule is unchanged: the DOM is written once, the model is read from it and
never the reverse. `onUpdate` replaces `onTrixChange`; `ui.nonce` still recreates
the section elements when the model changes underneath (undo, redo, applying the
Source tab), and the editor is torn down and remounted with them.

The dialog is down to **two** things — a picture has to be chosen from
somewhere, and a formula is LaTeX you want to see rendered before you commit to
it. Everything else is one click.

## Colour and size

Three sizes (small / normal / large) and three Oya roles (accent / success /
warning), applied to the selection.

**Both are CSS classes, never inline styles**, and that is not a preference: the
server strips `style=` from every document, so an inline-style mark would vanish
on the first save. `@tiptap/extension-color` writes `style="color: …"`, which is
exactly why cyprian defines its own two-line `CyInk` and `CySize` marks instead
of vendoring two more packages. A class survives the sanitiser, follows the
platform theme in both directions, and still means something when the PDF prints
it in ink.

`sanitize_html.ALLOWED_CLASSES` is the list, and it is an allowlist: a document
cannot carry a class the stylesheet has never heard of, and cannot borrow one
from the surrounding page either.

The same three roles are also a **section** tint, on the section bar: they
colour its heading, and older documents may carry a `cy-highlight` block from
version 1 which is tinted with them. `document_format` stores an invented colour
as no colour at all, so a hand-edited file cannot introduce one the stylesheet
has never heard of.

The paper itself follows the theme on screen — `.cy-screen` is what switches it,
and the PDF never sets that class, because a dark page is the one thing an export
must not do.

## The Source tab

A document **is** one XML file, so this is not a debug panel: it is the
document, in the form it is stored and backed up in, in ACE.

Applying goes through the **server's** parser (`document_source` →
`document_format.loads`) rather than a second parser written in JavaScript. One
parser, one set of rules, and a malformed file gets the same sentence the save
endpoint would give it. The endpoint parses and returns; it does not write — so
applying is an ordinary edit that goes through undo and autosave, and a bad
paste can be undone rather than being already on disk.

It is also where a table is edited. Tables still render on every surface, but
the cell-by-cell grid is no longer built in the canvas: it needed row and column
controls and a selection model of its own, which was more editor than the rest
of the writer put together.

Formulas are ordinary LaTeX in a textarea with KaTeX rendering live above, and
the rendered markup is cached in the file so the PDF has something to show —
WeasyPrint runs no JavaScript. The cache is sanitised on save because it arrives
from a browser; if nothing survives, the block falls back to its source.

Saving is autosave on idle plus Save and `Ctrl+S`, carrying the `content_hash`
it started from so a second tab gets a 409 rather than silently losing. The body
is read with `request.read()`, never `request.body`, which is capped at Django's
2.5 MB default — a document with a few embedded images is past that.

## Tests

```bash
cd zenobia/zenobia && BUILD_CYPRIAN=1 BUILD_EDITOR=1 \
  DJANGO_SETTINGS_MODULE=zenobia.settings python manage.py test \
  toto.cyprian.tests.test_format toto.cyprian.tests.test_views \
  toto.cyprian.tests.test_sanitize toto.cyprian.tests.test_tiptap
```

`toto` is a PEP 420 namespace package, so the modules are named explicitly —
`manage.py test toto.cyprian` discovers nothing. They are listed one by one in
`zenobia/scripts/clean_env_test.sh`, and delta runs the same four modules from
its own gate.

## Contracts

`toto.notarius` owns a contract — the parties, the signatures, the audit trail —
and its own body editor is a textarea holding Markdown. So the prose is edited
here instead: **Edit content** on a contract opens its body in the writer, and
notarius keeps the buttons that are genuinely its own (Generate PDF, Sign).

- The companion document is created once, beside the contract, and remembers
  which one it belongs to in `meta["contract"]` — which round-trips through the
  format for free, so the link survives a download, an edit by hand and a
  restore from backup.
- Saving writes the body back into the contract as `text/html`, so notarius's
  own Generate PDF renders what was just written. That is what makes this the
  editor rather than a copy of the text.
- A stale link is silent: a document that outlived its contract is still a
  document, and refusing to save it would be losing work over a broken pointer.
- `notarius/render.py` sanitises the HTML again on the way out, because a
  `.contract` can arrive by upload.

The button is absent, not broken, on a build without `BUILD_CYPRIAN`.

## Not in this pass

Footnotes, cross-references, comments and track-changes. Multi-column layout.
Import from `.docx` or Markdown. Collaborative editing — TipTap's Y.js path
needs a websocket and a second vendored dependency. Column resizing and per-cell
table styling. Going back to version 1: the conversion is one way, by decision. Publishing to a blog — there is no blog in this
monorepo to publish into. Building a table in the canvas: the Source tab edits
one, and that is the trade this made deliberately.
