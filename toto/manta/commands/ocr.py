from django import forms

from ..forms import _w
from .backends import TesseractCommand


class OcrForm(forms.Form):
    language = forms.CharField(
        initial="eng",
        widget=forms.TextInput(attrs=_w({"placeholder": "eng (or eng+pol)"})),
        help_text="Tesseract language code(s).",
    )


class OcrCommand(TesseractCommand):
    key = "ocr"
    label = "OCR (image → text)"
    inputs = {"image": {"file_type": "image", "name": "Image file"}}
    outputs = {"text": {"file_type": "text", "extension": "txt", "name": "Extracted text"}}
    form_class = OcrForm

    def describe(self, *, input_name, params=None):
        lang = (params or {}).get("language") or "eng"
        return f"tesseract ocr {input_name}  (lang={lang})  ->  .txt"
