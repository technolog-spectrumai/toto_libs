from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class ThumbnailForm(forms.Form):
    at_time = forms.CharField(initial="00:00:01", widget=forms.TextInput(attrs=_w({"placeholder": "00:00:01"})))
    output_name = forms.CharField(initial="thumbnail", widget=forms.TextInput(attrs=_w({"placeholder": "thumbnail"})))


class ThumbnailCommand(FfmpegCommand):
    key = "thumbnail"
    label = "Thumbnail"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "image", "extension": "jpg", "name": "Thumbnail image"}}
    form_class = ThumbnailForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.jpg"
        return CommandSpec([builders.build_thumbnail(input_name, out, p.get("at_time", "00:00:01"))], (out,))
