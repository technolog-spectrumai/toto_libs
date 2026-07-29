import os

from .. import builders
from .backends import FfprobeCommand
from .base import CommandSpec


class ProbeCommand(FfprobeCommand):
    key = "probe"
    label = "Probe → JSON file"
    inputs = {"media": {"file_type": "video", "name": "Media file"}}
    outputs = {"output": {"file_type": "json", "extension": "ffprobe.json", "name": "Probe JSON"}}
    form_class = None

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        base = os.path.splitext(input_name)[0] or "probe"
        return CommandSpec([builders.build_probe(input_name)], (f"{base}.ffprobe.json",))
