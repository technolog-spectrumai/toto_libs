from django import forms
from django.utils.translation import gettext_lazy as _

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class VstackForm(forms.Form):
    output_name = forms.CharField(initial="vstack", widget=forms.TextInput(attrs=_w({"placeholder": "vstack"})))


class VstackCommand(FfmpegCommand):
    key = "vstack"
    label = _("Stack vertically")
    inputs = {
        "top_video": {"file_type": "video", "name": "Top video"},
        "bottom_video": {"file_type": "video", "name": "Bottom video"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Vertically stacked video"}}
    form_class = VstackForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_vstack(input_name, self.secondary(extra_input_names), out)], (out,))
