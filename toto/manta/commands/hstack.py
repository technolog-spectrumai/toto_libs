from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class HstackForm(forms.Form):
    output_name = forms.CharField(initial="hstack", widget=forms.TextInput(attrs=_w({"placeholder": "hstack"})))


class HstackCommand(FfmpegCommand):
    key = "hstack"
    label = "Stack horizontally"
    inputs = {
        "left_video": {"file_type": "video", "name": "Left video"},
        "right_video": {"file_type": "video", "name": "Right video"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Horizontally stacked video"}}
    form_class = HstackForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_hstack(input_name, self.secondary(extra_input_names), out)], (out,))
