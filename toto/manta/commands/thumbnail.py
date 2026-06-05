from .base import BaseCommand, CommandSpec
from .. import builders
from ..forms import ThumbnailForm


class ThumbnailCommand(BaseCommand):
    key = "thumbnail"
    label = "Thumbnail"
    inputs = {"video": {"file_type": "video", "name": "Input video"}}
    outputs = {"output": {"file_type": "image", "extension": "jpg", "name": "Thumbnail image"}}
    form_class = ThumbnailForm

    def build_spec(self, *, input_name, extra_input_names=None, params=None):
        p = params or {}
        out = f"{self.output_name(p)}.jpg"
        return CommandSpec([builders.build_thumbnail(input_name, out, p.get("at_time", "00:00:01"))], (out,))
