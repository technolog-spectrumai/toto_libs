# toto.memo

File-based **presentation** viewer and browser editor. Memo renders and edits
self-contained presentation `.pml` (Presentation Markup Language) files stored in
the vault — it has no database models of its own.

## How it works

A presentation is a single self-contained XML document
(`file_type="presentation"`) parsed by [`presentation_format.py`](presentation_format.py):

```xml
<?xml version="1.0" encoding="utf-8"?>
<presentation version="1" title="My Talk">
  <slide>
    <title>Welcome</title>
    <body><![CDATA[
      <p>HTML content</p>
      <img src="data:image/png;base64,...">      <!-- raster, embedded ≤640px -->
      <svg ...>...</svg>                          <!-- vector, inlined verbatim -->
    ]]></body>
  </slide>
</presentation>
```

The file is the single source of truth (same model as `.tpy` notebooks in
`toto.mandragora`): parsed when the viewer/editor opens, serialized back on Save.
Slide bodies are CDATA-wrapped so pasted HTML / `<img>` data URIs / inline
`<svg>` stay literal. Each `<slide>` becomes one reveal.js `<section>`.

## Entry points

- **Vault Play button** → `memo:present` — reveal.js slideshow viewer
  (registered via `plugins/vault_play_plugins.py`).
- **Vault Edit button** → `memo:edit` — in-browser slide editor with client-side
  image resize (≤640px) + base64 embedding and SVG inlining
  (registered via `plugins/vault_editor_plugins.py`).
- `memo:save` — persists edited slides back to the vault file as XML.
- `memo:index` — gallery of presentations the user can open.

New presentations are created from the vault's *New File → presentation* menu
(`vault.CreateEmptyFileView` seeds a blank document and routes to the editor).

## Detection

The dedicated `.pml` extension is what marks a file as a presentation —
`VaultFile._EXT_MAP` maps `.pml` → `presentation` (extension-first, the same way
`.tpy` → `notebook`). Generic `.xml` files stay typed `xml`. `.pml` is plain XML
internally; the extension just disambiguates intent.

## Trust

The viewer renders slide bodies as raw HTML (`|safe`), the same trust model as
serving an uploaded `.html`/`.svg` vault file. Presentations are owner-authored
and viewing respects vault visibility.

## Dependencies

- `vault` — presentations are `VaultFile`s; play/editor plugins wire the buttons.
- reveal.js (`static/vendor/reveal/`) — slideshow rendering.
