from django import forms

from .. import builders
from ..forms import _w
from .backends import FfmpegCommand
from .base import CommandSpec


class ExtractMp3Form(forms.Form):
    BITRATE_CHOICES = [("128k", "128k"), ("192k", "192k (default)"), ("256k", "256k"), ("320k", "320k")]
    bitrate = forms.ChoiceField(choices=BITRATE_CHOICES, initial="192k", widget=forms.Select(attrs=_w()))
    output_name = forms.CharField(initial="audio", widget=forms.TextInput(attrs=_w({"placeholder": "audio"})))


class ExtractMp3Command(FfmpegCommand):
    key = "extract_mp3"
    label = "Extract MP3"
    inputs = {"media": {"file_type": "video", "name": "Source video"}}
    outputs = {"output": {"file_type": "audio", "extension": "mp3", "name": "Extracted MP3"}}
    form_class = ExtractMp3Form

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.mp3"
        return CommandSpec([builders.build_extract_mp3(input_name, out, p.get("bitrate", "192k"))], (out,))
