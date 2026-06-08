from django import forms

from ..forms import _w
from .backends import TesseractCommand

# Common Tesseract language packs (3-letter codes). Availability depends on the
# installed traineddata, but these cover the usual cases.
LANGUAGE_CHOICES = [
    ("eng", "English"), ("pol", "Polish"), ("deu", "German"), ("fra", "French"),
    ("spa", "Spanish"), ("ita", "Italian"), ("por", "Portuguese"), ("nld", "Dutch"),
    ("rus", "Russian"), ("ukr", "Ukrainian"), ("ces", "Czech"), ("swe", "Swedish"),
    ("chi_sim", "Chinese (Simplified)"), ("jpn", "Japanese"), ("kor", "Korean"),
    ("ara", "Arabic"),
]


class OcrForm(forms.Form):
    language = forms.ChoiceField(
        choices=LANGUAGE_CHOICES,
        initial="eng",
        widget=forms.Select(attrs=_w()),
        help_text="Language of the text in the image.",
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
