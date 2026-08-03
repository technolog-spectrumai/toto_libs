# toto.memo

Presentations: a block editor, a reveal.js player, and a PDF/ZIP export — all
over one self-contained XML file in the vault.

## Purpose

A deck is **one `VaultFile`**, `file_type="presentation"`, holding the whole
slideshow: images embedded as base64 `data:` URIs, SVGs inlined verbatim. There
is nothing in the database — memo owns no models at all. That makes a deck
movable, downloadable, backup-able and shareable with the tools the vault
already has, and it means a presentation can never half-exist.

## Screens

| Screen | What it is |
|---|---|
| **Gallery** (`/memo/`) | Every deck you can open, each card showing a real thumbnail of slide one. Paginated. |
| **Editor** (`/memo/edit/<pk>/`) | The block canvas. |
| **Player** (`/memo/present/<pk>/`) | reveal.js, fullscreen, no site chrome. |

The vault's **Play** and **Edit** buttons on a deck go to the player and the
editor. There is no longer a raw-XML surface.

## The editing surface is the slide

The middle of the editor is not a preview of a slide, it **is** one: the same
`_slide.html` partial and the same `slide.css` the player uses, laid out at its
true 1280×720 and scaled to fit with a CSS transform. Where a line breaks in the
editor is where it breaks when presenting.

That is worth stating because the thing it replaces looked similar and was not.
The old editor's preview pane was a `<div>` that copied *two hex colours* and
*one `img { max-width }` rule* out of reveal's theme by hand. Editing and
presenting drifted apart the first time either was touched.

```
slide.css ──► the editor canvas   .memo-slide
          ──► present.html        .reveal section > .memo-slide
          ──► the PDF export      one @page per slide
          ──► the gallery         a scaled-down thumbnail
```

**Reveal's own theme is deliberately not loaded.** `theme-black.css` and
`theme-white.css` put their variables on `:root` and set global typography — on
the editor page that fights the oya chrome, and in the gallery it is impossible,
because several decks with different themes appear on one page and `:root` can
hold only one of them. `slide.css` therefore declares those variables itself as
two scoped classes, copied from reveal's files. That is the only duplication in
the design, it is about twenty lines, and it buys per-element theming. Reveal
keeps what it is good at: transitions, controls, progress and hash navigation.

## Format v2

```xml
<presentation version="2" title="Quarterly review" theme="black">
  <slide id="s-a1b2c3d4" layout="two-column">
    <title>Findings</title>
    <block id="b-11aa" type="list" slot="left">
      <item><![CDATA[Growth strongest in EMEA]]></item>
    </block>
    <block id="b-11ab" type="image" slot="right" alt="Chart"><![CDATA[data:image/png;base64,…]]></block>
  </slide>
</presentation>
```

- **Layouts**: `title-content`, `two-column`, `full-bleed`, `section`, `quote`.
- **Blocks**: `heading`, `text`, `list`, `image`, `svg`, `code`, `quote`, plus
  `html` — the escape hatch, hidden from the add menu.
- **Every payload is CDATA-wrapped**, with no per-type exceptions. It reads
  noisier than escaped text and it is the only rule that cannot lose a byte,
  including a literal `]]>` (split across two sections, as v1 already did).
- `<title>` stays a slide *element*, not a block: the filmstrip, the gallery card
  and the outline all read it, and as a block each of them would have to go
  hunting for "the heading that is really the title".

**Stable ids are load-bearing, not decoration.** They are the `:key` for every
`x-for` in the editor. Alpine keyed by index reuses DOM nodes by position, so
reordering two blocks leaves the browser's text nodes where they were while the
model swaps underneath — the text and the model diverge on the next keystroke.
Index keys and drag-and-drop are mutually exclusive; ids are how you get both.

### v1 decks

A v1 slide (`<title>` + one blob of `<body>` HTML) is upgraded **in memory** to a
single `type="html"` block, which the player renders through the same unescaped
path v1 used — so an old deck presents byte-identically. **The file is not
rewritten on open**; it stays v1 until the user saves.

### Nothing unknown is discarded

v1's parser read four fields and dropped the rest of the tree, so the format
could not be extended by hand and a newer document lost data the moment an older
build saved it. v2 preserves and re-emits unknown attributes, unknown block
types and unknown child elements. There is a test that says so.

## Sanitisation

Slide content is sanitised **on the server**, in `sanitize.py`, on both the save
path and the open path. Open matters too: anyone can upload an XML file to the
vault and open it as a deck, and a public one renders in every visitor's gallery.

Hand-rolled on `html.parser` rather than adding `bleach` — neither it nor `nh3`
is in any host's requirements. Disallowed tags are **unwrapped, never dropped**,
so pasting from a word processor loses the styling and keeps the words.

Two rules that look like bugs and are not:

- **`code` blocks are not sanitised.** Running them through the HTML allowlist
  would delete `<script>alert(1)</script>` *with its contents* — the exact
  snippet somebody pasted in to show. Safety comes from escaping at render
  instead, which is correct and lossless.
- **`html` blocks are left verbatim.** Their trust model is exactly what v1's
  `{{ slide.body|safe }}` already was, and rewriting somebody's hand-written
  slide would be the worse bargain. They render **escaped** in the gallery
  though — that page lists other people's public decks, and live markup there
  would be a strictly worse exposure than the player.

`media.clean_svg_markup` used to be four regexes. A regex cannot see attributes,
so `<svg onload="…">`, `javascript:` hrefs and external `<use href="//host/x#y">`
all went straight through into the player. It now delegates to a real parser.
SVG attribute case is preserved explicitly — `html.parser` lowercases names, and
a `viewBox` that becomes `viewbox` silently loses the graphic's coordinate
system.

## Drag and drop

Four targets, one engine (`static/memo/drag.js`), hand-rolled on pointer events
because no DnD library is vendored and adding one means editing three hosts'
`download_vendor.py` manifests:

| Drag | Zone |
|---|---|
| a slide, to reorder | the filmstrip |
| a block, to reorder **or to move between columns** | each `.memo-flow` |
| an image from the vault picker | each `.memo-flow` |
| a file from the desktop | each `.memo-flow` (native drop) |

Moving a block between the columns of a two-column layout is the *same*
operation as reordering it — `slot` is just another property — rather than a
special case to keep in step separately.

Shape lifted from `canasta/drag.js`: an 8px/400ms tap-vs-drag threshold (without
it every touch tap becomes a one-pixel drag and a block can never be selected),
a single `pointerId` lock, `setPointerCapture`, and a proxy on a top layer so the
list does not reflow under the pointer. **Every drag has a click or keyboard
equivalent calling the same `MemoModel` function** — the house rule, and what
makes the editor usable without a pointing device.

## Editing text

`contenteditable`, on the text-bearing blocks only, with a selection popover.
The rule that makes it work:

> the DOM is written **once**, when the element is created, and after that the
> model is updated from the DOM and never the reverse.

Writing back into a focused `contenteditable` moves the caret to the start on
every keystroke. When the model does change underneath — undo, redo, an import —
the elements are recreated instead, by bumping a nonce that is part of every
`:key`.

## Undo, and saving

Undo is **whole-document snapshots**, not a command log: `contenteditable`
produces mutations the app never observes (a paste, an autocorrect), so a command
log drifts out of step within a session. Fifty entries, coalesced within 600 ms
by key, so typing a sentence is one undo step. `Ctrl+Z` inside a text field is
deliberately **not** intercepted — the browser's own undo knows about the caret.

Saving is autosave on idle (2s, with a 30s ceiling), plus Save, `Ctrl+S`, and
`visibilitychange`. Each save carries the `content_hash` it started from; the
endpoint answers **409** if the file moved on, and the editor offers reload or
overwrite rather than silently winning.

**The save endpoint reads the body with `request.read()`, not `request.body`.**
`request.body` is checked against `DATA_UPLOAD_MAX_MEMORY_SIZE`, which no host
sets and so defaults to 2.5 MB — a deck with about twenty embedded images was
already past it, and saving one raised `RequestDataTooBig` before the view ran.
Autosave would have made that constant rather than occasional. There is a test
that saves a 4 MB deck.

## Export

- **PDF** — WeasyPrint, rendered from `slide.css`, one page per slide at the same
  1280×720. Lazily imported and gated by `BUILD_WEASYPRINT`, exactly as
  `notarius/render.py` does it; without it the endpoint answers 503 with a
  sentence naming the flag.
- **ZIP** — `presentation.xml` plus `assets/` as real files, with each image and
  SVG block's path in a `src` attribute instead of inline base64. Small,
  readable, diffable. **Import re-inlines the exported bytes without resizing
  them** — re-running the 640px thumbnailer each cycle would shrink every picture
  a little more. Import always creates a **new** deck: a restore that can destroy
  the deck you were protecting is the wrong shape for a backup.

## Layout of the app

| File | What it is |
|---|---|
| `presentation_format.py` | the v2 document: parse, serialise, upgrade, validate |
| `sanitize.py` | the server allowlists (HTML and SVG) |
| `bundle.py` | ZIP export and import |
| `render_pdf.py` | WeasyPrint, gated |
| `media.py` | data-URI conversion both ways, SVG cleaning |
| `templates/memo/_slide.html` | one slide, rendered once, for every surface |
| `static/memo/slide.css` | how a slide looks, for every surface |
| `static/memo/{model,history,sanitize,canvas,drag,editor}.js` | the editor |

memo is the first app in `toto-works` to ship static files. Two traps come with
that: `MANIFEST.in` is an **extension allowlist** and `build_wheels.py --sdist`
builds the wheel *from the sdist*, so an unlisted extension works locally and
vanishes only in a host's clean-env gate; and a directory named `media/`,
`build/`, `dist/` or `staticfiles/` anywhere under `static/` is gitignored at any
depth and would never be committed at all — note this app already has a
`media.py`, which makes `static/memo/media/` a very natural and fatal choice.

## Tests

```bash
cd /tmp && DJANGO_SETTINGS_MODULE=toto.memo.testing.settings \
    python -m django test toto.memo.tests
```

`toto` is a PEP 420 namespace package, so the label must name the test **module**
— `toto.memo` alone discovers nothing. The suite runs in zenobia's clean-env gate
against the installed wheels; until `testing/settings.py` existed it was run by
no gate at all.
