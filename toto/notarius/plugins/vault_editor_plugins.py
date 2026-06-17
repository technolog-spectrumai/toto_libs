from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="contract", title="Contract", order=35)
class ContractVaultEditorPlugin(VaultEditorPlugin):
    """Opens a ``.contract`` vault file in the browser form editor."""

    file_type = "contract"

    def get_editor_url(self, vault_file) -> str:
        return reverse("notarius:edit", args=[vault_file.pk])
