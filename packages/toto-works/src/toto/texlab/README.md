# toto.texlab

*(Requires BUILD_LATEX=1)*

File-based **LaTeX workspace**. Documents are plain `.tex` vault files
(`file_type="latex"`, plus `.bib` bibliographies) — the vault file is the
single source of truth, the same model as `.pml` presentations (`toto.memo`)
and `.tpy` notebooks (`toto.mandragora`). The old `LatexWorkspace`/`LatexFile`
models were dropped in migration `0002_remove_workspace_models`; only
`CompileRun` (per-file compile audit) remains in the database.

## Entry points

- `texlab:index` — the LaTeX workspace: lists `.tex`/`.bib` vault files the
  user can open, with one-click *New document* (`texlab:create`). Linked from
  the dashboard as the **LaTeX** card.
- **Vault Edit button** → `texlab:file_display` — Ace editor (latex mode) with
  Save, compile trigger and compile history
  (registered via `plugins/vault_editor_plugins.py`).
- `texlab:compile_latex` / `texlab:compile_status` — start a compile
  (Celery/workflow-backed when available, synchronous fallback otherwise) and
  poll its `CompileRun`.

New documents are created from the workspace's *New document* button (seeds
`BLANK_TEX_DOCUMENT`) or the vault's *New File → latex* menu
(`vault.CreateEmptyFileView` mirrors the same blank skeleton).

## Detection

`VaultFile._EXT_MAP` maps `.tex`/`.sty`/`.cls`/`.dtx`/`.ins` → `latex` and
`.bib` → `bib` (extension-first). The files stay plain text and are editable
as such.

## Compilation

`compile.py` runs `pdflatex -interaction=nonstopmode` in a tempdir over the
file (plus sibling bucket files), storing the output PDF as a `VaultFile` and
the log on the `CompileRun`. Requires TeX Live on the host/container
(`INSTALL_TEXLIVE=1` in deploy configs).

## Dependencies

- `vault` — documents and output PDFs are `VaultFile`s; editor/play plugins
  wire the buttons.
- `workflows` — compiles can run as workflow nodes (`CompileRun.workflow_run`).
