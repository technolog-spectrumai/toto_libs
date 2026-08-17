from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="pxml", title="Presentation", order=30)
class PresentationVaultPlayPlugin(VaultPlayPlugin):
    """Opens a .pxml deck as a reveal.js slideshow.

    `key` must equal the file_type: `for_file_type` is `registry.get(file_type)`,
    an exact dict lookup. This registration and vault migration 0021 are one
    release — either without the other leaves every deck with no Play button.
    """

    file_type = "pxml"

    def get_play_url(self, vault_file) -> str:
        return reverse("memo:present", args=[vault_file.pk])


@VaultPlayPlugin.plugin(key="presentation", title="Presentation", order=31)
class LegacyPresentationVaultPlayPlugin(PresentationVaultPlayPlugin):
    """The same button for rows still typed with the legacy spelling.

    Migration 0021 retypes what it can reach, but it deliberately skips mirrored
    stubs (re-stamped by the peer on every refresh), rows in s3/remote buckets
    (unreadable from a migration) and encrypted decks (unidentifiable). Six
    lines here is the difference between those rows keeping a Play button and
    silently losing one — and a distinct key is legal, since BasePlugin.register
    only refuses a duplicate KEY.
    """

    file_type = "presentation"
