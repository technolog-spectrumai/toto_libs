from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="latex", title="TeX Editor", order=10)
class LatexEditorPlugin(VaultEditorPlugin):
    file_type = "latex"

    def get_editor_url(self, vault_file) -> str:
        return reverse("texlab:file_display", args=[vault_file.pk])
