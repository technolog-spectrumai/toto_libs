from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="python", title="Python Editor", order=20)
class PythonEditorPlugin(VaultEditorPlugin):
    file_type = "python"

    def get_editor_url(self, vault_file) -> str:
        return reverse("antaresia:file_display", args=[vault_file.pk])
