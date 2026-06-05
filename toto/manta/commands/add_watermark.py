from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class AddWatermarkForm(forms.Form):
    POSITION_CHOICES = [
        ("top-left", "Top left"), ("top-right", "Top right"),
        ("bottom-left", "Bottom left"), ("bottom-right", "Bottom right (default)"), ("center", "Center"),
    ]
    position = forms.ChoiceField(choices=POSITION_CHOICES, initial="bottom-right", widget=forms.Select(attrs=_w()))
    output_name = forms.CharField(initial="watermarked", widget=forms.TextInput(attrs=_w({"placeholder": "watermarked"})))


class AddWatermarkCommand(FfmpegCommand):
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
        return CommandSpec([builders.build_add_watermark(input_name, self.secondary(extra_input_names), out, p.get("position", "bottom-right"))], (out,))
