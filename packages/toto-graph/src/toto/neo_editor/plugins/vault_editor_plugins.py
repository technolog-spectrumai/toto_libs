from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="neojson", title="NeoJSON Editor", order=25)
class NeoJsonEditorPlugin(VaultEditorPlugin):
    """Opens a .neojson vault file in the dual-mode (JSON + graph) NeoJSON editor."""

    file_type = "neojson"

    def get_editor_url(self, vault_file) -> str:
        return reverse("neo_editor:neojson_editor", args=[vault_file.pk])
