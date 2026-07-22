from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="presentation", title="Presentation", order=30)
class PresentationVaultPlayPlugin(VaultPlayPlugin):
    """Opens a presentation .pml vault file as a reveal.js slideshow."""

    file_type = "presentation"

    def get_play_url(self, vault_file) -> str:
        return reverse("memo:present", args=[vault_file.pk])
