from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="latex", title="LaTeX Compiler", order=30)
class LatexVaultPlayPlugin(VaultPlayPlugin):
    """Opens a .tex vault file in the texplay compile-and-preview viewer."""

    file_type = "latex"

    def get_play_url(self, vault_file) -> str:
        return reverse("texplay:latex_play", args=[vault_file.pk])
