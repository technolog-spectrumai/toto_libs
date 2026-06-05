"""
Pure ffmpeg/ffprobe command spec factory.

This module is intentionally free of Django, DB, filesystem and subprocess
access. It wraps the hardcoded argv builders in ``builders.py`` (the same ones
the runner executes) so the *preview* shown to the user can never drift from
what actually runs, while staying safe to call from a request handler.

Inputs are **display names** (e.g. ``"clip.mp4"``), never real filesystem
paths, so ``FFmpegCommandSpec.shell_display`` cannot leak vault internals.
The runner resolves trusted paths separately at execution time.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field

from . import builders


# Operations the builder understands, in display order. Mirror the predefined
# tasks wired through ``CELERY_TASK_REGISTRY`` / runner ``_build``.
OPERATIONS: tuple[str, ...] = (
    "compress",
    "resize",
    "crop",
    "change_fps",
    "cut",
    "extract_mp3",
    "remove_audio",
    "replace_audio",
    "add_subtitles",
    "add_watermark",
    "thumbnail",
    "gif",
    "vstack",
    "hstack",
    "concat",
    "probe",
)

OPERATION_LABELS: dict[str, str] = {
    "compress": "Compress",
    "resize": "Resize",
    "crop": "Crop",
    "change_fps": "Change FPS",
    "cut": "Cut / trim",
    "extract_mp3": "Extract MP3",
    "remove_audio": "Remove audio",
    "replace_audio": "Replace audio",
    "add_subtitles": "Add subtitles",
    "add_watermark": "Add watermark",
    "thumbnail": "Thumbnail",
    "gif": "Animated GIF",
    "vstack": "Stack vertically",
    "hstack": "Stack horizontally",
    "concat": "Concatenate",
    "probe": "Probe → JSON file",
}

# Operations that consume exactly one extra (secondary) input file.
SECONDARY_OPERATIONS: frozenset[str] = frozenset({
    "replace_audio", "add_subtitles", "add_watermark", "vstack", "hstack",
})


@dataclass(frozen=True)
class FFmpegCommandSpec:
    """An immutable, fully-resolved command preview.

    ``commands`` holds one argv per ffmpeg/ffprobe invocation — normally a
    single command, but two for ``gif`` (palette pass + encode pass).
    """

    operation: str
    commands: tuple[tuple[str, ...], ...]
    output_names: tuple[str, ...] = field(default_factory=tuple)

    @property
    def shell_display(self) -> str:
        return "\n".join(shlex.join(list(c)) for c in self.commands)


class UnknownOperation(ValueError):
    """Raised when an unsupported operation is requested."""


class FFmpegCommandFactory:
    """Build an :class:`FFmpegCommandSpec` from display names + params. Pure."""

    def build(
        self,
        operation: str,
        *,
        input_name: str,
        extra_input_names: list[str] | None = None,
        params: dict | None = None,
    ) -> FFmpegCommandSpec:
        params = params or {}
        output_name = str(params.get("output_name") or "output")

        if operation == "probe":
            base = os.path.splitext(input_name)[0] or "probe"
            return self._spec(operation, [builders.build_probe(input_name)], (f"{base}.ffprobe.json",))

        if operation == "compress":
            out = f"{output_name}.mp4"
            argv = builders.build_compress(input_name, out, params.get("quality", "medium"))
            return self._spec(operation, [argv], (out,))

        if operation == "resize":
            out = f"{output_name}.mp4"
            argv = builders.build_resize(
                input_name, out,
                int(params.get("width", -2)),
                int(params.get("height", -2)),
            )
            return self._spec(operation, [argv], (out,))

        if operation == "cut":
            out = f"{output_name}.mp4"
            argv = builders.build_cut(
                input_name, out,
                str(params["start_time"]),
                str(params["end_time"]),
            )
            return self._spec(operation, [argv], (out,))

        if operation == "extract_mp3":
            out = f"{output_name}.mp3"
            argv = builders.build_extract_mp3(input_name, out, params.get("bitrate", "192k"))
            return self._spec(operation, [argv], (out,))

        if operation == "thumbnail":
            out = f"{output_name}.jpg"
            argv = builders.build_thumbnail(input_name, out, params.get("at_time", "00:00:01"))
            return self._spec(operation, [argv], (out,))

        if operation == "gif":
            out = f"{output_name}.gif"
            palette = "palette.png"
            start = str(params.get("start_time", "00:00:00"))
            duration = str(params.get("duration", "5"))
            fps = int(params.get("fps", 12))
            width = int(params.get("width", 480))
            palette_argv = builders.build_gif_palette(input_name, palette, start, duration, fps, width)
            gif_argv = builders.build_gif(input_name, palette, out, start, duration, fps, width)
            return self._spec(operation, [palette_argv, gif_argv], (out,))

        if operation == "crop":
            out = f"{output_name}.mp4"
            argv = builders.build_crop(
                input_name, out,
                int(params.get("width", 0)), int(params.get("height", 0)),
                int(params.get("x", 0)), int(params.get("y", 0)),
            )
            return self._spec(operation, [argv], (out,))

        if operation == "change_fps":
            out = f"{output_name}.mp4"
            argv = builders.build_change_fps(input_name, out, int(params.get("fps", 30)))
            return self._spec(operation, [argv], (out,))

        if operation == "remove_audio":
            out = f"{output_name}.mp4"
            return self._spec(operation, [builders.build_remove_audio(input_name, out)], (out,))

        if operation == "replace_audio":
            out = f"{output_name}.mp4"
            argv = builders.build_replace_audio(input_name, self._secondary(extra_input_names), out)
            return self._spec(operation, [argv], (out,))

        if operation == "add_subtitles":
            out = f"{output_name}.mp4"
            argv = builders.build_add_subtitles(input_name, self._secondary(extra_input_names), out)
            return self._spec(operation, [argv], (out,))

        if operation == "add_watermark":
            out = f"{output_name}.mp4"
            argv = builders.build_add_watermark(
                input_name, self._secondary(extra_input_names), out,
                params.get("position", "bottom-right"),
            )
            return self._spec(operation, [argv], (out,))

        if operation == "vstack":
            out = f"{output_name}.mp4"
            argv = builders.build_vstack(input_name, self._secondary(extra_input_names), out)
            return self._spec(operation, [argv], (out,))

        if operation == "hstack":
            out = f"{output_name}.mp4"
            argv = builders.build_hstack(input_name, self._secondary(extra_input_names), out)
            return self._spec(operation, [argv], (out,))

        if operation == "concat":
            out = f"{output_name}.mp4"
            # Individual sources live inside the concat list file, not the argv.
            argv = builders.build_concat("concat.txt", out, bool(params.get("reencode", False)))
            return self._spec(operation, [argv], (out,))

        raise UnknownOperation(f"Unknown operation: {operation!r}")

    @staticmethod
    def _secondary(extra_input_names) -> str:
        names = extra_input_names or []
        return names[0] if names else "second.mp4"

    @staticmethod
    def _spec(operation, commands, output_names) -> FFmpegCommandSpec:
        return FFmpegCommandSpec(
            operation=operation,
            commands=tuple(tuple(c) for c in commands),
            output_names=tuple(output_names),
        )
