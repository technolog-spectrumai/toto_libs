"""The vault's Edit button for a document.

`key` MUST equal `file_type`: the registry stores by key and
`VaultEditorPlugin.for_file_type()` is `registry.get(file_type)`, so a mismatch
makes the plugin silently unreachable. That equality is also why documents
needed their own file type — `key="xml"` belongs to `toto.editor` and
`BasePlugin.register` raises on a duplicate, so a document typed 'xml' could
never have won this button.
"""

from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin

from ..surface import document_editor_shown


@VaultEditorPlugin.plugin(key="document", title="Document", order=28)
class DocumentEditorPlugin(VaultEditorPlugin):
    file_type = "document"

    @classmethod
    def should_register(cls) -> bool:
        # A host that hides the writer hides the way in from the vault too —
        # otherwise the file manager still hands out the one door the dashboard
        # just closed. The editor's own URLs stay mounted for the apps that
        # drive it; this is the browsable surface, and it goes with the tile.
        return document_editor_shown()

    def get_editor_url(self, vault_file) -> str:
        return reverse("cyprian:edit", args=[vault_file.pk])
