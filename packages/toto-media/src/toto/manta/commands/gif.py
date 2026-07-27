from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class GifForm(forms.Form):
    start_time = forms.CharField(initial="00:00:00", widget=forms.TextInput(attrs=_w({"placeholder": "00:00:00"})))
    duration = forms.IntegerField(initial=5, widget=forms.NumberInput(attrs=_w({"placeholder": "5"})))
    fps = forms.IntegerField(initial=12, widget=forms.NumberInput(attrs=_w({"placeholder": "12"})))
    width = forms.IntegerField(initial=480, widget=forms.NumberInput(attrs=_w({"placeholder": "480"})))
    output_name = forms.CharField(initial="animation", widget=forms.TextInput(attrs=_w({"placeholder": "animation"})))


class GifCommand(FfmpegCommand):
    key = "gif"
    label = "Animated GIF"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "gif", "extension": "gif", "name": "Animated GIF"}}
    form_class = GifForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.gif"
        palette = "palette.png"
        start = str(p.get("start_time", "00:00:00"))
        dur = str(p.get("duration", "5"))
        fps = int(p.get("fps", 12))
        width = int(p.get("width", 480))
        c1 = builders.build_gif_palette(input_name, palette, start, dur, fps, width)
        c2 = builders.build_gif(input_name, palette, out, start, dur, fps, width)
        return CommandSpec([c1, c2], (out,))
