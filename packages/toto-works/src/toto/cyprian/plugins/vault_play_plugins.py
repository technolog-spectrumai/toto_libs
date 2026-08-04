"""The vault's Play button for a document — opens the reader."""

from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="document", title="Document", order=28)
class DocumentPlayPlugin(VaultPlayPlugin):
    file_type = "document"

    def get_play_url(self, vault_file) -> str:
        return reverse("cyprian:read", args=[vault_file.pk])
