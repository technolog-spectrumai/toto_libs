from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="presentation", title="Presentation source", order=30)
class PresentationEditorPlugin(VaultEditorPlugin):
    """Opens a presentation .pml vault file as plain XML text.

    In the vault a presentation is just an editable text file; the structured
    slide editor stays reachable from the memo app (index/player/source page).
    """

    file_type = "presentation"

    def get_editor_url(self, vault_file) -> str:
        return reverse("memo:source", args=[vault_file.pk])
