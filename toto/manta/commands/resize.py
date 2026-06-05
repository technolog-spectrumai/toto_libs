from django import forms

from .. import builders
from ..forms import _w, _XBIND
from .backends import FfmpegCommand
from .base import CommandSpec


class ResizeForm(forms.Form):
    preserve_aspect_ratio = forms.BooleanField(
        required=False, initial=True,
        widget=forms.CheckboxInput(attrs={"class": "rounded border", "x-bind:class": _XBIND, "x-model": "preserve"}),
        help_text="Provide only one dimension; the other is computed automatically.",
    )
    width = forms.IntegerField(required=False, initial=1280,
        widget=forms.NumberInput(attrs=_w({"placeholder": "1280", "x-bind:required": "!preserve"})))
    height = forms.IntegerField(required=False,
        widget=forms.NumberInput(attrs=_w({"placeholder": "720", "x-bind:required": "!preserve"})))
    output_name = forms.CharField(initial="resized", widget=forms.TextInput(attrs=_w({"placeholder": "resized"})))

    def clean(self):
        data = super().clean()
        preserve = data.get("preserve_aspect_ratio", False)
        width, height = data.get("width"), data.get("height")
        if preserve:
            if width and height:
                raise forms.ValidationError("Provide only one dimension when preserving aspect ratio.")
            if not width and not height:
                raise forms.ValidationError("Provide either width or height when preserving aspect ratio.")
            data["width"] = width or -2
            data["height"] = height or -2
        else:
            if not width:
                self.add_error("width", "Width is required when not preserving aspect ratio.")
            if not height:
                self.add_error("height", "Height is required when not preserving aspect ratio.")
        return data


class ResizeCommand(FfmpegCommand):
    key = "resize"
    label = "Resize"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "video", "extension": "mp4", "name": "Resized video"}}
    form_class = ResizeForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp4"
        return CommandSpec([builders.build_resize(input_name, out, int(p.get("width", -2)), int(p.get("height", -2)))], (out,))
