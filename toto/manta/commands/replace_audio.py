from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ReplaceAudioForm


class ReplaceAudioCommand(BaseCommand):
    key = "replace_audio"
    label = "Replace audio"
    inputs = {
        "video": {"file_type": "video", "name": "Video file"},
        "audio": {"file_type": "audio", "name": "Replacement audio"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Video with replaced audio"}}
    form_class = ReplaceAudioForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_replace_audio(
            input_name, self.secondary(extra_input_names), out)], (out,))
