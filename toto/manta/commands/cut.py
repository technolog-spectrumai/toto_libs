from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class CutForm(forms.Form):
    start_time = forms.CharField(initial="00:00:00", widget=forms.TextInput(attrs=_w({"placeholder": "00:00:10"})))
    end_time = forms.CharField(initial="00:00:30", widget=forms.TextInput(attrs=_w({"placeholder": "00:00:30"})))
    output_name = forms.CharField(initial="clip", widget=forms.TextInput(attrs=_w({"placeholder": "clip"})))


class CutCommand(FfmpegCommand):
    key = "cut"
    label = "Cut / trim"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Trimmed video"}}
    form_class = CutForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_cut(input_name, out, str(p.get("start_time", "00:00:00")), str(p.get("end_time", "00:00:30")))], (out,))
