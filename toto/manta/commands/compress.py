from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import CompressForm


class CompressCommand(BaseCommand):
    key = "compress"
    label = "Compress"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Compressed video"}}
    form_class = CompressForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_compress(input_name, out, p.get("quality", "medium"))], (out,))
