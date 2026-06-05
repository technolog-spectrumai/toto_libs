from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import CropForm


class CropCommand(BaseCommand):
    key = "crop"
    label = "Crop"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Cropped video"}}
    form_class = CropForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_crop(
            input_name, out, int(p.get("width", 0)), int(p.get("height", 0)),
            int(p.get("x", 0)), int(p.get("y", 0)))], (out,))
