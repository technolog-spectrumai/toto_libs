from .base import BaseCommand
from ..forms import OcrForm


class OcrCommand(BaseCommand):
    key = "ocr"
    label = "OCR (image → text)"
    backend = "service"
    service_key = "ocr"
    inputs = {"image": {"file_type": "image", "name": "Image file"}}
    outputs = {"text": {"file_type": "text", "extension": "txt", "name": "Extracted text"}}
    form_class = OcrForm

    def describe(self, *, input_name, params=None):
        lang = (params or {}).get("language") or "eng"
        return f"ocr {input_name}  (tesseract, lang={lang})  ->  .txt"
