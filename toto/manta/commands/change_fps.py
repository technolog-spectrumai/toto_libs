from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ChangeFpsForm


class ChangeFpsCommand(BaseCommand):
    key = "change_fps"
    label = "Change FPS"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "FPS-adjusted video"}}
    form_class = ChangeFpsForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_change_fps(input_name, out, int(p.get("fps", 30)))], (out,))
