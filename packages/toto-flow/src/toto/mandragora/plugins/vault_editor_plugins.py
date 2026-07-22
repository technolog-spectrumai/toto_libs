from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="notebook", title="Notebook", order=15)
class NotebookEditorPlugin(VaultEditorPlugin):
    """Opens a .tpy vault file in the mandragora notebook editor."""

    file_type = "notebook"

    def get_editor_url(self, vault_file) -> str:
        return reverse("mandragora:tpy_display", args=[vault_file.pk])
