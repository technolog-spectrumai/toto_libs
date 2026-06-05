from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ExtractMp3Form


class ExtractMp3Command(BaseCommand):
    key = "extract_mp3"
    label = "Extract MP3"
    inputs = {"media": {"file_type": "video", "name": "Source video"}}
    outputs = {"output": {"file_type": "audio", "extension": "mp3", "name": "Extracted MP3"}}
    form_class = ExtractMp3Form

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp3"
        return CommandSpec([builders.build_extract_mp3(input_name, out, p.get("bitrate", "192k"))], (out,))
