from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class AddSubtitlesForm(forms.Form):
    output_name = forms.CharField(initial="subtitled", widget=forms.TextInput(attrs=_w({"placeholder": "subtitled"})))


class AddSubtitlesCommand(FfmpegCommand):
    key = "add_subtitles"
    label = "Add subtitles"
    inputs = {
        "video": {"file_type": "video", "name": "Video file"},
        "subtitles": {"file_type": "subtitle", "name": "Subtitle file"},
    }
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Video with subtitles"}}
    form_class = AddSubtitlesForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_add_subtitles(input_name, self.secondary(extra_input_names), out)], (out,))
