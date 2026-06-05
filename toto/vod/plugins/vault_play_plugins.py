from django.urls import reverse

from toto.vault.plugins import VaultPlayPlugin


@VaultPlayPlugin.plugin(key="video", title="Video Player", order=10)
class VideoVaultPlayPlugin(VaultPlayPlugin):
    file_type = "video"

    def get_play_url(self, vault_file) -> str:
        return reverse("vod:vault_file_play", args=[vault_file.pk])


@VaultPlayPlugin.plugin(key="audio", title="Audio Player", order=20)
class AudioVaultPlayPlugin(VaultPlayPlugin):
    file_type = "audio"

    def get_play_url(self, vault_file) -> str:
        return reverse("vod:vault_file_play", args=[vault_file.pk])
