"""The vault's Play button for a document — opens the reader."""

from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin

from ..surface import document_editor_shown


@VaultPlayPlugin.plugin(key="document", title="Document", order=28)
class DocumentPlayPlugin(VaultPlayPlugin):
    file_type = "document"

    @classmethod
    def should_register(cls) -> bool:
        # Goes with the Edit button, for the same reason.
        return document_editor_shown()

    def get_play_url(self, vault_file) -> str:
        return reverse("cyprian:read", args=[vault_file.pk])
