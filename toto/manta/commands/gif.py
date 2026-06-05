from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import GifForm


class GifCommand(BaseCommand):
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
