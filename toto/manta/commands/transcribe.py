from django import forms

from ..forms import _w
from .backends import WhisperCommand


class TranscribeForm(forms.Form):
    language = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs=_w({"placeholder": "en — leave blank to auto-detect"})),
        help_text="Language code (optional).",
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
