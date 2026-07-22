from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="text", title="Text Editor", order=30)
class TextEditorPlugin(VaultEditorPlugin):
    file_type = "text"

    def get_editor_url(self, vault_file) -> str:
        return reverse("editor:text_display", args=[vault_file.pk])


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
# not here — its editor URL (sketch:svg_file_display) is only mounted when BUILD_SKETCH=1,
# so registering it alongside toto.editor (BUILD_LATEX/BUILD_PYEDITOR) would 500 the vault
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

    def get_editor_url(self, vault_file) -> str:
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
