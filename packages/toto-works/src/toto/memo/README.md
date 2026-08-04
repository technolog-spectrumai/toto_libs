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

**Reveal is initialised with `center: false`, and that is load-bearing.** Its
default centring positions each `<section>` from the section's *measured*
height, and reveal's sections are absolutely positioned with `height: auto` — so
a slide sized as a percentage of that collapses to nothing, taking every layout
that depends on the 720px box with it. With centring off, reveal pins the
section to the top-left of the stage it has already sized to exactly
1280 × 720, and the real box fills it. The slide is never sized in percentages
on any surface.

**Reveal's own theme is deliberately not loaded.** `theme-black.css` and
`theme-white.css` put their variables on `:root` and set global typography — on
the editor page that fights the oya chrome, and in the gallery it is impossible,
because several decks with different themes appear on one page and `:root` can
hold only one of them. `slide.css` therefore declares those variables itself as
two scoped classes, copied from reveal's files. That is the only duplication in
the design, it is about twenty lines, and it buys per-element theming. Reveal
keeps what it is good at: transitions, controls, progress and hash navigation.

## Layouts are boxes, and boxes are the whole geometry model

A slide's `layout` names a fixed set of **boxes**, in reading order. Nothing in
a deck resizes, moves or nests one: you pick a layout, you get its boxes, and
any box takes any kind of block — text, a picture, an SVG, code, a formula, a
quote.

| Layout | Boxes |
|---|---|
| `title-content`, `section`, `quote`, `full-bleed` | one |
| `two-column` | left, right |
| `three-column` | left, middle, right |
| `image-left` / `image-right` | media, body (the picture box is the wider one) |
| `two-row` | top, bottom |
| `grid` | a, b, c, d |
| `lead` | lead, body — one big thing and the sentence explaining it |

That is a deliberate trade against free geometry. Draggable, resizable boxes
make every slide a small layout project, and a deck of thirty ends up with
thirty slightly different margins — the thing that makes homemade decks look
homemade. **More layouts is the answer to "I need another shape", not draggable
corners.** Adding one is a `LAYOUT_SLOTS` entry and a grid definition in
`slide.css`; a test asserts every named box has a `grid-area`, because a box the
stylesheet does not place lands wherever the browser feels like.

A block whose `slot` this layout does not have still appears — in the FIRST box,
never nowhere — and keeps its own slot in the file. So trying two columns, going
back to one, and returning puts every block where it was. `Slide.columns` in
`presentation_format.py` and `columns()` in `model.js` are the two
implementations of that rule, and `test_the_javascript_agrees_about_the_boxes`
is what keeps them the same.

What the editor adds on top is only *which box* — asked once, above the block
buttons, and only when there is more than one. Empty boxes are drawn dashed
while editing (never when presenting), because a layout whose second column is
invisible until something is in it looks like a layout that did not apply.

## Fonts, sizing and formulas

**Font** is a deck property, like the theme: five CSS system stacks
(`sans`, `serif`, `mono`, `rounded`, `condensed`). System stacks only, because no
webfont is vendored, `slide.css` may not use `url()`, and the PDF is rendered by
WeasyPrint inside a container — a family the image lacks would substitute
silently and the export would stop matching the screen. Every stack therefore
ends in a generic, and names the DejaVu families `notarius/render.py` already
relies on. The classes are written `.memo-slide.memo-font-serif`, not bare, so
they beat the theme rule regardless of source order.

**Sizing.** A block carries `scale`, which is `auto`, `auto:0.80` or `0.80`. The
editor measures the block against the space it has and writes the resolved
number back — the player and the PDF cannot measure, so they can only render
what the editor decided. The intent rides along with the measurement precisely
so that storing a number never silently pins a block that should keep re-fitting.
Auto-fit only shrinks; the manual override goes both ways, to 1.6.

The multiplier is `--memo-block-scale`, **not** `--memo-scale`: that one is the
stage's zoom, set on an ancestor, and custom properties inherit — sharing the
name would shrink every block's text by the canvas zoom as well.

**Overflow.** Below the 0.5 floor the content no longer fits at all. Everywhere
except the editor it clips, as it must for the geometry to stay exact; in the
editor it spills past the border, dimmed and flagged, because clipping there
hides the very thing you need to fix.

**Formulas** are a block type, written as ordinary LaTeX in a textarea with
KaTeX rendering live above it. There is no equation builder — what is stored is
exactly what you typed — and a folded-away cheatsheet inserts examples at the
cursor. `throwOnError: false`, so a half-typed formula shows the broken fragment
in red rather than blanking.

The rendered markup is **cached in the document**, in a `<render>` child beside
`<source>`, because WeasyPrint runs no JavaScript and an exported deck would
otherwise lose every formula. Both parts are child elements, never element text
beside a child: mixed content would let the source silently absorb the
indentation whitespace around its sibling. The cache is derived and untrusted —
it comes from a browser — so `sanitize_katex()` strips it to KaTeX's own tags,
`katex-` classes and a short list of metric styles. If nothing survives, the
block falls back to showing its source, never a blank.

KaTeX is vendored into `core/static/vendor/katex/`, CSS plus ~60 font files, by
each host's `download_vendor.py`. That directory is gitignored and fetched at
image build, so its absence is normal — and the PDF path checks for the fonts as
well as the stylesheet, because a stylesheet without its fonts renders every
glyph as a box, which is worse than showing the source.

## Format v2

```xml
<presentation version="2" title="Quarterly review" theme="black" font="serif">
  <slide id="s-a1b2c3d4" layout="two-column">
    <title>Findings</title>
    <block id="b-11aa" type="list" slot="left">
      <item><![CDATA[Growth strongest in EMEA]]></item>
    </block>
    <block id="b-11ab" type="image" slot="right" alt="Chart" scale="auto:0.80"><![CDATA[data:image/png;base64,…]]></block>
    <block id="b-11ac" type="formula">
      <source><![CDATA[E = mc^2]]></source>
      <render><![CDATA[<span class="katex">…</span>]]></render>
    </block>
  </slide>
</presentation>
```

- **Layouts**: `title-content`, `two-column`, `full-bleed`, `section`, `quote`.
- **Blocks**: `heading`, `text`, `list`, `image`, `code`, `quote`,
  `formula`, plus `svg` and `html` — both real block types, neither offered in
  the toolbar. An SVG is a *picture*: you insert it with **Image**, from the
  vault or an upload, and the block becomes an `svg` when the payload turns out
  to be markup rather than a data URI. Asking a writer which kind of picture
  file they were about to choose was a question with no useful answer.
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

## Nothing on a slide is dragged

Dragging is gone from the presentation editor, deliberately and entirely: no
block handles, no drop zones, no proxy, no drag.js on the page. A test asserts
the attributes are absent, because half-removed dragging is worse than either
state.

It was the only way to end up with a deck whose boxes disagreed with its
layout — and every gesture it offered already had a button:

| Was a drag | Is now |
|---|---|
| move a block between columns | pick the box, pick the content type |
| reorder blocks in a box | a box holds one thing |
| reorder slides | the filmstrip's ↑ ↓ |
| drop a picture onto a slide | the box's Image dialog: vault or upload |

## A box owns its content

Selecting a box puts a small toolbar on it: the seven content types, then
**Edit** and **Clear**. Choosing a type is one click — it does not delete and
re-add, and switching between heading, text and quote KEEPS the words, because
those three are the same thing with different type. Switching to a formula or a
picture does not, because pretending LaTeX is a sentence would put
`\frac{a}{b}` on the slide as prose.

**A box holds one thing.** The format still reads several blocks in one slot and
still renders them, but the editor works one-per-box: it is what makes "what
kind of content is this" a property of the box rather than a list to manage, and
it is why there is no add, remove or reorder inside a box at all.

**Clear empties the box; the box stays** — the layout says it exists.

## Everything is written in the dialog

The canvas is 1280×720 scaled to fit a browser window. That is a fine place to
*see* a slide and a hopeless place to write LaTeX into or paste an SVG into, so
the canvas renders and the dialog writes:

| Content | The dialog gives you |
|---|---|
| heading, text, quote | Trix — bold, italics, links, lists |
| list | one item per line, which is how people type a list |
| code, the v1 `html` | ACE, XML or plain-text mode |
| formula | LaTeX with a live KaTeX preview and the cheatsheet |
| image, svg | your vault, or an upload, with alt text and fit — one dialog for both, previewing a data URI in an `<img>` and markup inline |

Because nothing is edited in place, what the canvas shows is always exactly what
the file holds — a slide can no longer be half-edited, and the autofit
measurement never races a caret.

## Editing text

Two `contenteditable` fields are left on the page — the slide title, and Trix
inside the dialog — and the rule that makes both work is unchanged:

> the DOM is written **once**, when the element is created, and after that the
> model is updated from the DOM and never the reverse.

Writing back into a focused field moves the caret to the start on every
keystroke. When the model does change underneath — undo, redo, an import, a
dialog being applied — the elements are recreated instead, by bumping a nonce
that is part of every `:key`.

The selection popover is gone with the inline editing it belonged to: Trix has
its own toolbar, and the `execCommand` calls the popover was built on are
deprecated with no replacement that can edit content.

## Undo, and saving

Undo is **whole-document snapshots**, not a command log: `contenteditable`
produces mutations the app never observes (a paste, an autocorrect), so a command
log drifts out of step within a session. Fifty entries, coalesced within 600 ms
by key, so typing a sentence is one undo step. The copy is **deferred** onto that
same window rather than taken per keystroke — a deck with twenty embedded images
is several MB per `structuredClone`. The model updates synchronously, so nothing
is ever at risk; only the copy waits. Anything that reads history (undo, redo)
settles the pending snapshot first. `Ctrl+Z` inside a text field is
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
| `templates/memo/_formula_help.html` | the click-to-insert LaTeX cheatsheet |
| `static/memo/slide.css` | how a slide looks, for every surface |
| `static/memo/{model,history,sanitize,canvas,drag,editor}.js` | the editor |

memo is the first app in `toto-works` to ship static files. Two traps come with
that: `MANIFEST.in` is an **extension allowlist** and `build_wheels.py --sdist`
builds the wheel *from the sdist*, so an unlisted extension works locally and
vanishes only in a host's clean-env gate; and a directory named `media/`,
`build/`, `dist/` or `staticfiles/` anywhere under `static/` is gitignored at any
depth and would never be committed at all — note this app already has a
`media.py`, which makes `static/memo/media/` a very natural and fatal choice.

## Two things that look like details and are not

**A press on a control is never a drag.** `drag.js` ignores a `pointerdown` that
starts on a `button`, link, field or `[data-no-drag]`. Without that guard, any
control nested inside a drag handle is simply dead: the handle lookup finds the
ancestor, `setPointerCapture` retargets the pointerup to it, and the browser
never synthesises a `click` on the button. That is what killed Delete, Duplicate
and the reorder arrows in the filmstrip, where the whole row is the handle.

**Off-screen thumbnails build nothing.** A thumbnail is a real slide — same
markup, same stylesheet — which is what makes it honest and also what made a
forty-slide deck build forty slide DOMs. One `IntersectionObserver` on the list
reports into reactive component state (`ui.live`), not into a DOM attribute:
Alpine tracks its own data and would never re-evaluate an `x-if` that read an
attribute an observer had changed behind its back.

## Tests

```bash
cd /tmp && DJANGO_SETTINGS_MODULE=toto.memo.testing.settings \
    python -m django test toto.memo.tests
```

`toto` is a PEP 420 namespace package, so the label must name the test **module**
— `toto.memo` alone discovers nothing. The suite runs in zenobia's clean-env gate
against the installed wheels; until `testing/settings.py` existed it was run by
no gate at all.
