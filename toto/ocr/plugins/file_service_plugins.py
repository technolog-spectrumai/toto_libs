from __future__ import annotations

import os
import tempfile

from django.urls import reverse

from toto.fileservices.plugin import FileServicePlugin
from toto.fileservices.runner import save_output, stage_input


@FileServicePlugin.plugin(key="ocr_tool", title="OCR (text extraction)", order=10)
class OcrBuilderPlugin(FileServicePlugin):
    """Visible vault-wand entry for images. Hands the user off to the standalone
    OCR page, which runs the actual extraction through the 'ocr' executor below
    (wrapped in the fileservices-run workflow)."""

    accepted_file_types = ["image"]
    icon = "fa-solid fa-file-lines"
    description = "Extract text from an image. Opens the OCR page; each run is tracked as a workflow."
    builder = True

    def builder_url(self, vault_file) -> str:
        return reverse("ocr:home") + f"?file={vault_file.pk}"

    def execute(self, run):
        raise NotImplementedError("OCR runs through its page, not inline.")


@FileServicePlugin.plugin(key="ocr", title="OCR (text extraction)", order=30)
class OcrFileServicePlugin(FileServicePlugin):
    # Backend-only: the menu entry + UI live in the manta builder (ocr command);
    # this plugin just runs tesseract via FileServiceRun.
    listed = False
    accepted_file_types = ["image"]
    icon = "fa-solid fa-eye"
    description = "Extract text from an image with Tesseract. The result is saved as a .txt file."
    args_label = "Language code(s)"
    args_placeholder = "eng    (or eng+pol)"
    args_required = False

    def execute(self, run) -> list[int]:
        from toto.ocr.ocr import OcrHelper

        lang = (run.args or "").strip() or "eng"
        helper = OcrHelper(lang)

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = stage_input(run.input_file, tmpdir)
            data = helper.run_tesseract(image_path)
            lines = helper.extract_lines(data)
            text = "\n".join(line["text"] for line in lines).strip()

            run.stdout = f"Extracted {len(lines)} line(s), {len(text)} characters (lang={lang})."

            base = os.path.splitext(run.input_file.title or "ocr")[0]
            out_name = f"{base}.ocr.txt"
            out_path = os.path.join(tmpdir, out_name)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(text)
            vf = save_output(run, out_path, out_name, "text")
            return [vf.pk]
