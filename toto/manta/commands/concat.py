from django import forms

from .. import builders
from ..forms import _w, _XBIND
from .backends import FfmpegCommand
from .base import CommandSpec


class ConcatForm(forms.Form):
    reencode = forms.BooleanField(
        required=False, initial=False,
        widget=forms.CheckboxInput(attrs={"class": "rounded border", "x-bind:class": _XBIND}),
        help_text="Re-encode (slower, but handles incompatible streams).",
    )
    output_name = forms.CharField(initial="merged", widget=forms.TextInput(attrs=_w({"placeholder": "merged"})))


class ConcatCommand(FfmpegCommand):
    key = "concat"
    label = "Concatenate"
    inputs = {"videos": {"file_type": "video", "name": "Videos to concatenate", "multiple": True}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Concatenated video"}}
    form_class = ConcatForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_concat("concat.txt", out, bool(p.get("reencode", False)))], (out,))
