from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ResizeForm


class ResizeCommand(BaseCommand):
    key = "resize"
    label = "Resize"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Resized video"}}
    form_class = ResizeForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_resize(
            input_name, out, int(p.get("width", -2)), int(p.get("height", -2)))], (out,))
