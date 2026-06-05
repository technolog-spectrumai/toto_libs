from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ConcatForm


class ConcatCommand(BaseCommand):
    key = "concat"
    label = "Concatenate"
    inputs = {"videos": {"file_type": "video", "name": "Videos to concatenate", "multiple": True}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Concatenated video"}}
    form_class = ConcatForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_concat("concat.txt", out, bool(p.get("reencode", False)))], (out,))
