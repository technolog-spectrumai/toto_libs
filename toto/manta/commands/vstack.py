from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import VstackForm


class VstackCommand(BaseCommand):
    key = "vstack"
    label = "Stack vertically"
    inputs = {
        "top_video": {"file_type": "video", "name": "Top video"},
        "bottom_video": {"file_type": "video", "name": "Bottom video"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Vertically stacked video"}}
    form_class = VstackForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_vstack(
            input_name, self.secondary(extra_input_names), out)], (out,))
