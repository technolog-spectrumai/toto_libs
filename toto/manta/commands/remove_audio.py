from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import RemoveAudioForm


class RemoveAudioCommand(BaseCommand):
    key = "remove_audio"
    label = "Remove audio"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Muted video"}}
    form_class = RemoveAudioForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_remove_audio(input_name, out)], (out,))
