from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="contract", title="Contract", order=35)
class ContractVaultPlayPlugin(VaultPlayPlugin):
    """Opens a ``.contract`` signing-document vault file in the contract viewer."""

    file_type = "contract"

    def get_play_url(self, vault_file) -> str:
        return reverse("notarius:view", args=[vault_file.pk])
