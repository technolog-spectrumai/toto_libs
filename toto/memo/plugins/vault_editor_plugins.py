from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="presentation", title="Presentation", order=30)
class PresentationEditorPlugin(VaultEditorPlugin):
    """Opens a presentation .pml vault file in the browser slide editor."""

    file_type = "presentation"

    def get_editor_url(self, vault_file) -> str:
        return reverse("memo:edit", args=[vault_file.pk])
