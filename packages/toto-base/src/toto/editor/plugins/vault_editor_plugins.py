from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="text", title="Text Editor", order=30)
class TextEditorPlugin(VaultEditorPlugin):
    file_type = "text"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:text_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="markdown", title="Markdown Editor", order=31)
class MarkdownEditorPlugin(VaultEditorPlugin):
    """Markdown edits in ACE, next to Text in the menu (order 31 vs 30)."""

    file_type = "markdown"
    new_file_extension = ".md"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:markdown_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="json", title="JSON Editor", order=40)
class JsonEditorPlugin(VaultEditorPlugin):
    file_type = "json"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:json_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="yaml", title="YAML Editor", order=45)
class YamlEditorPlugin(VaultEditorPlugin):
    file_type = "yaml"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:yaml_display", args=[vault_file.pk])


# NOTE: the SVG editor plugin lives in toto.sketch (sketch/plugins/vault_editor_plugins.py),
# listing on any SVG file when sketch is off.


@VaultEditorPlugin.plugin(key="xml", title="XML Editor", order=46)
class XmlEditorPlugin(VaultEditorPlugin):
    file_type = "xml"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:xml_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="csv", title="CSV Editor", order=47)
class CsvEditorPlugin(VaultEditorPlugin):
    file_type = "csv"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:csv_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="html", title="HTML Editor", order=48)
class HtmlEditorPlugin(VaultEditorPlugin):
    file_type = "html"
    #: Declared since 2026-08-29 for Office's Documents tab (retired to limbo
    #: 2026-09-02); kept because new_file_extension is what lets ANY surface
    #: that consults the editor registry seed an .html document — the vault's
    #: New menu included, not just the hub that first wanted it.
    new_file_extension = ".html"

    def blank_content(self, title: str) -> str:
        """A whole page, not an empty string.

        `blank_content` must return something the app's own reader accepts, and
        for HTML the reader is a browser: a bare fragment renders as unstyled
        text the moment anybody downloads the file. The page mirrors what
        `toto.cyprian.htmldoc.page` writes for a wiki page, so a document
        created here and a document created by the writer are the same shape.
        """
        stem = (title or "").rsplit(".", 1)[0].strip() or "Document"
        safe = (stem.replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))
        return (
            '<!doctype html>\n<html>\n<head>\n<meta charset="utf-8">\n'
            f"<title>{safe}</title>\n"
            "<style>\nbody { font-family: Georgia, 'Times New Roman', serif; "
            "margin: 3rem auto; max-width: 46rem; line-height: 1.5; }\n"
            "</style>\n</head>\n<body>\n"
            f"<h1>{safe}</h1>\n<p></p>\n</body>\n</html>\n"
        )

    def get_editor_url(self, vault_file) -> str:
        """The HTML source editor. Always, on every host.

        This used to return `cyprian:edit_html` wherever cyprian was installed
        — which is everywhere — so pressing Edit on a page silently minted a
        rich-text twin and opened THAT, with a bridge rewriting the page on
        every save. Edit is now what it says: it opens this file, as HTML.

        There is no second format to become. CTML was retired on 2026-08-29;
        a written document IS an HTML file, edited here, with a live preview
        beside the source and `toto.aralia` for the PDF.
        """
        return reverse("editor:html_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="latex", title="LaTeX", order=49)
class LatexEditorPlugin(VaultEditorPlugin):
    """Edit .tex source in Ace with LaTeX highlighting. Compilation is the
    separate TeX Compiler (toto.texlab), not this editor."""
    file_type = "latex"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:latex_display", args=[vault_file.pk])


@VaultEditorPlugin.plugin(key="bib", title="BibTeX", order=50)
class BibEditorPlugin(VaultEditorPlugin):
    """Edit .bib bibliographies in Ace with BibTeX highlighting."""
    file_type = "bib"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:bib_display", args=[vault_file.pk])
