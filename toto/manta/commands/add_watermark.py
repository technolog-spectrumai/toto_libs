from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import AddWatermarkForm


class AddWatermarkCommand(BaseCommand):
    key = "add_watermark"
    label = "Add watermark"
    inputs = {
        "video": {"file_type": "video", "name": "Video file"},
        "watermark": {"file_type": "image", "name": "Watermark image"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Watermarked video"}}
    form_class = AddWatermarkForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_add_watermark(
            input_name, self.secondary(extra_input_names), out, p.get("position", "bottom-right"))], (out,))
