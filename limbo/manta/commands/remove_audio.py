from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class RemoveAudioForm(forms.Form):
    output_name = forms.CharField(initial="muted", widget=forms.TextInput(attrs=_w({"placeholder": "muted"})))


class RemoveAudioCommand(FfmpegCommand):
    key = "remove_audio"
    label = "Remove audio"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Muted video"}}
    form_class = RemoveAudioForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_remove_audio(input_name, out)], (out,))
