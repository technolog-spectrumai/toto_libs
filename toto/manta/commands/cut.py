from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import CutForm


class CutCommand(BaseCommand):
    key = "cut"
    label = "Cut / trim"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Trimmed video"}}
    form_class = CutForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_cut(
            input_name, out, str(p.get("start_time", "00:00:00")),
            str(p.get("end_time", "00:00:30")))], (out,))
