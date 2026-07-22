# LaTeX editing moved to the generic ACE editor (toto.editor registers the
# `latex` and `bib` VaultEditorPlugins with syntax highlighting). texlab is now
# the "TeX Compiler" — it compiles .tex vault files to PDF via a Celery workflow
# and no longer provides its own editor plugin. Intentionally registers nothing.
