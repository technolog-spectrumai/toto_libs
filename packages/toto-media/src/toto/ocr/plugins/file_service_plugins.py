"""The vault's wand offers "Read the text" on an image or a PDF.

`builder = True` is the whole trick, and it is manta's: a builder plugin
redirects to its own page instead of running through `FileServiceRun`, so the
vault lists it even on a host with no `toto.fileservices` — the run substrate is
ffmpeg-shaped and lives in another wheel that most hosts decline.

`accepts()` already refuses encrypted files and non-local content, so neither is
restated here.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.vault.plugins import FileServicePlugin


@FileServicePlugin.plugin(key="ocr", title="Read the text", order=30)
class OcrFileServicePlugin(FileServicePlugin):
    accepted_file_types = ["image", "pdf"]
    icon = "fa-solid fa-file-signature"
    description = _("Read the text out of this file, and copy, download or "
                    "save it.")
    builder = True

    def builder_url(self, vault_file) -> str:
        return reverse("ocr:builder") + f"?file={vault_file.pk}"

    def execute(self, run):
        raise NotImplementedError(
            "OCR runs through its own page, not the file-service substrate.")
