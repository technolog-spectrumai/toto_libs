from django.urls import reverse

from toto.vault.plugins import VaultEditorPlugin


@VaultEditorPlugin.plugin(key="sheet", title="Open in Primula", order=20)
class SheetEditorPlugin(VaultEditorPlugin):
    """Deep-links a ``sheet`` vault file into the Primula spreadsheet editor.

    A Primula sheet is stored as a JSON workbook snapshot, but the vault should open
    it in the Univer grid rather than raw ACE — same idea as memo's presentation
    source plugin. Primula stays fully reachable at ``primula:index`` independently.
    """

    file_type = "sheet"

    def get_editor_url(self, vault_file) -> str:
        return reverse("primula:edit", args=[vault_file.pk])
