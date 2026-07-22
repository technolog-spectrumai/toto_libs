from django import forms

from ..forms import _w
from .backends import WhisperCommand

# Curated set of common Whisper languages; blank = auto-detect.
LANGUAGE_CHOICES = [
    ("", "Auto-detect"),
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("nl", "Dutch"), ("pl", "Polish"),
    ("ru", "Russian"), ("uk", "Ukrainian"), ("cs", "Czech"), ("sv", "Swedish"),
    ("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean"), ("ar", "Arabic"),
    ("hi", "Hindi"), ("tr", "Turkish"),
]


class TranscribeForm(forms.Form):
    language = forms.ChoiceField(
        choices=LANGUAGE_CHOICES,
        required=False,
        initial="",
        widget=forms.Select(attrs=_w()),
        help_text="Leave on auto-detect unless Whisper guesses wrong.",
    )


class TranscribeCommand(WhisperCommand):
    key = "transcribe"
    label = "Transcribe (speech → text)"
    inputs = {"audio": {"file_type": "audio", "name": "Audio file"}}
    outputs = {
        "text": {"file_type": "text", "extension": "txt", "name": "Transcript"},
        "subtitles": {"file_type": "text", "extension": "srt", "name": "Subtitles"},
    }
    form_class = TranscribeForm

    def describe(self, *, input_name, params=None):
        lang = (params or {}).get("language") or "auto"
        return f"whisper transcribe {input_name}  (language={lang})  ->  .txt + .srt"
