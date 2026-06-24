from __future__ import annotations

from django.urls import reverse

from toto.fileservices.plugin import FileServicePlugin


@FileServicePlugin.plugin(key="manta", title="Manta (media tools)", order=10)
class MantaServicePlugin(FileServicePlugin):
    """Single menu entry for all manta commands (ffmpeg/ffprobe/transcribe).

    Selecting it hands the user off to the manta command builder, where they
    pick a command, preview it, and run it. Registered only when toto.manta is
    installed (BUILD_MANTA), so the vault wand never offers a dead link.
    """

    accepted_file_types = ["video", "audio"]
    icon = "fa-solid fa-film"
    description = "Build, preview and run media / transcribe commands on the builder page."
    builder = True

    def builder_url(self, vault_file) -> str:
        return reverse("manta:command_builder") + f"?file={vault_file.pk}&service=manta"

    def execute(self, run):
        raise NotImplementedError("Manta runs through its command builder, not inline.")
