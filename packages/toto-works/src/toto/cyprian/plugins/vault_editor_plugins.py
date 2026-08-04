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


@VaultEditorPlugin.plugin(key="document", title="Document", order=28)
class DocumentEditorPlugin(VaultEditorPlugin):
    file_type = "document"

    def get_editor_url(self, vault_file) -> str:
        return reverse("cyprian:edit", args=[vault_file.pk])
