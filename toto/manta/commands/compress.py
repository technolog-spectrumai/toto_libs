from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class CompressForm(forms.Form):
    QUALITY_CHOICES = [
        ("tiny", "Tiny (smallest)"), ("small", "Small"),
        ("medium", "Medium (default)"), ("high", "High"), ("archive", "Archive (best)"),
    ]
    quality = forms.ChoiceField(choices=QUALITY_CHOICES, initial="medium", widget=forms.Select(attrs=_w()))
    output_name = forms.CharField(initial="compressed", widget=forms.TextInput(attrs=_w({"placeholder": "compressed"})))


class CompressCommand(FfmpegCommand):
    key = "compress"
    label = "Compress"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Compressed video"}}
    form_class = CompressForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_compress(input_name, out, p.get("quality", "medium"))], (out,))
