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


@VaultEditorPlugin.plugin(key="svg", title="SVG Editor", order=50)
class SvgEditorPlugin(VaultEditorPlugin):
    file_type = "svg"

    def get_editor_url(self, vault_file) -> str:
        return reverse("sketch:svg_file_display", args=[vault_file.pk])


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
