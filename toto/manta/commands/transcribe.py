from .base import BaseCommand
from ..forms import TranscribeForm


class TranscribeCommand(BaseCommand):
    key = "transcribe"
    label = "Transcribe (speech → text)"
    backend = "service"
    service_key = "transcription"
    inputs = {"audio": {"file_type": "audio", "name": "Audio file"}}
    outputs = {
        "text": {"file_type": "text", "extension": "txt", "name": "Transcript"},
        "subtitles": {"file_type": "text", "extension": "srt", "name": "Subtitles"},
    }
    form_class = TranscribeForm

    def describe(self, *, input_name, params=None):
        lang = (params or {}).get("language") or "auto"
        return f"transcribe {input_name}  (whisper, language={lang})  ->  .txt + .srt"
