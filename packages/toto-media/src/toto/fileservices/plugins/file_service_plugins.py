from __future__ import annotations

import json
import mimetypes
import os
import tempfile

from toto.fileservices.plugin import FileServicePlugin
from toto.fileservices.runner import (
    run_subprocess,
    save_output,
    stage_input,
    tokenize_args,
)


@FileServicePlugin.plugin(key="ffmpeg", title="FFmpeg", order=11)
class FFmpegServicePlugin(FileServicePlugin):
    # Hidden from the menu (superseded by the Videomant builder) but kept
    # registered for direct/workflow execution from a raw arg string.
    listed = False
    accepted_file_types = ["video", "audio", "image"]
    icon = "fa-solid fa-film"
    description = "Run ffmpeg with custom arguments. The input is supplied as -i; end your args with an output filename."
    args_label = "ffmpeg arguments"
    args_placeholder = "-vf scale=640:-2 -c:v libx264 output.mp4"
    args_required = True

    def execute(self, run) -> list[int]:
        from toto.vault.models import VaultFile

        tokens = tokenize_args(run.args)
        if not tokens:
            raise ValueError("Provide ffmpeg arguments ending with an output filename.")

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = stage_input(run.input_file, tmpdir)
            before = set(os.listdir(tmpdir))

            argv = ["ffmpeg", "-y", "-i", input_path, *tokens]
            proc = run_subprocess(argv, cwd=tmpdir, timeout=7200)
            run.stdout = proc.stdout[-8000:]
            run.stderr = proc.stderr[-8000:]
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg exited {proc.returncode}:\n{proc.stderr[-2000:]}")

            produced = [f for f in os.listdir(tmpdir) if f not in before]
            if not produced:
                raise RuntimeError("ffmpeg produced no output file. End your arguments with an output filename.")

            out_pks: list[int] = []
            for fname in sorted(produced):
                path = os.path.join(tmpdir, fname)
                if not os.path.isfile(path):
                    continue
                mime, _ = mimetypes.guess_type(fname)
                file_type = VaultFile.detect_type(mime or "", fname)
                vf = save_output(run, path, fname, file_type)
                out_pks.append(vf.pk)
            return out_pks


@FileServicePlugin.plugin(key="ffprobe", title="FFprobe", order=21)
class FFprobeServicePlugin(FileServicePlugin):
    # Hidden from the menu (superseded by the Videomant builder) but kept
    # registered for direct/workflow execution.
    listed = False
    accepted_file_types = ["video", "audio", "image"]
    icon = "fa-solid fa-circle-info"
    description = "Inspect a media file with ffprobe. Output JSON is saved as a new file."
    args_label = "ffprobe arguments (optional)"
    args_placeholder = "-show_format -show_streams"
    args_required = False

    _DEFAULT = ["-v", "quiet", "-print_format", "json", "-show_format", "-show_streams"]

    def execute(self, run) -> list[int]:
        tokens = tokenize_args(run.args) if run.args.strip() else list(self._DEFAULT)

        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = stage_input(run.input_file, tmpdir)
            argv = ["ffprobe", *tokens, input_path]
            proc = run_subprocess(argv, cwd=tmpdir, timeout=300)
            run.stdout = proc.stdout[-16000:]
            run.stderr = proc.stderr[-8000:]
            if proc.returncode != 0:
                raise RuntimeError(f"ffprobe exited {proc.returncode}:\n{proc.stderr[-2000:]}")

            # Pretty-print JSON output when possible.
            content = proc.stdout
            try:
                content = json.dumps(json.loads(proc.stdout), indent=2)
            except Exception:
                pass

            base = os.path.splitext(run.input_file.title or "probe")[0]
            out_name = f"{base}.ffprobe.json"
            out_path = os.path.join(tmpdir, out_name)
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write(content)
            vf = save_output(run, out_path, out_name, "json")
            return [vf.pk]
