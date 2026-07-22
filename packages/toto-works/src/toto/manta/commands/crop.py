from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class CropForm(forms.Form):
    width = forms.IntegerField(initial=640, widget=forms.NumberInput(attrs=_w({"placeholder": "640"})))
    height = forms.IntegerField(initial=480, widget=forms.NumberInput(attrs=_w({"placeholder": "480"})))
    x = forms.IntegerField(initial=0, required=False, widget=forms.NumberInput(attrs=_w({"placeholder": "0"})))
    y = forms.IntegerField(initial=0, required=False, widget=forms.NumberInput(attrs=_w({"placeholder": "0"})))
    output_name = forms.CharField(initial="cropped", widget=forms.TextInput(attrs=_w({"placeholder": "cropped"})))

    def clean(self):
        data = super().clean()
        data["x"] = data.get("x") or 0
        data["y"] = data.get("y") or 0
        return data


class CropCommand(FfmpegCommand):
    key = "crop"
    label = "Crop"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Cropped video"}}
    form_class = CropForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_crop(input_name, out, int(p.get("width", 0)), int(p.get("height", 0)), int(p.get("x", 0)), int(p.get("y", 0)))], (out,))
